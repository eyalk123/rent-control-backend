"""Sentry error monitoring and backend performance tracing. No profiling, no replay.

A no-op when ``SENTRY_DSN`` is empty, which is the case in every test run and in local
development, so importing this module is always safe (``tests/conftest.py`` imports
``app.main``, which initialises Sentry at import time).

Tracing is **backend-only**: the web and mobile clients stay error-only, which is what
keeps them out of consent-banner territory and keeps the privacy policies true (see
``rent-control-web/DEPLOYMENT_CHECKLIST.md`` B7). Nothing here sends PII — see
``_before_send`` and the ``send_default_pii=False`` note below.
"""

import logging
import os
from typing import Any
from urllib.parse import parse_qsl, urlencode

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.config import settings

logger = logging.getLogger(__name__)

# X-Cron-Secret guards the /internal/* endpoints. sentry-sdk scrubs Authorization and
# Cookie automatically but knows nothing about this header, so it would otherwise be
# sent in clear text on any cron-job error event.
_EXTRA_SENSITIVE_HEADERS = {"x-cron-secret"}

# `send_default_pii=False` does NOT cover the query string: sentry-sdk attaches it
# verbatim (see `sentry_sdk.integrations._asgi_common._get_request_data` — the non-PII
# branch still sets `request["query_string"]`). The search box on `/transactions` and
# `/suppliers` sends `?q=<free text>`, which is where an owner types a renter or supplier
# name, so a 500 on a search would file that name in Sentry.
#
# An allowlist rather than a denylist, for two reasons. A new query parameter is filtered
# until someone decides it is safe, which is the right default for a product where most
# free text is tenant data. And the SDK hands us an already-unquoted query string, so a
# search for "Levi & Sons" arrives as `q=Levi & Sons` and splits into a `q` pair plus a
# stray `Sons` key — per-key redaction would leak the fragment, whereas an allowlist
# drops it.
#
# Keep this in sync with the `Query(...)` parameters in `app/api/routers/`: everything
# here is an id, an enum, a date or a paging value — the things that make an event
# reproducible — and nothing here is free text.
_ALLOWED_QUERY_PARAMS = frozenset(
    {
        "category_id",
        "format",
        "from_date",
        "include_inactive",
        "lang",
        "limit",
        "offset",
        "property_id",
        "renter_id",
        "to_date",
        "type",
        "year",
    }
)


def _filter_query_string(query_string: str) -> str:
    """Drop every query parameter that is not explicitly known to be free of user text.

    Returns a re-encoded string holding only allowlisted pairs, so `?q=Levi&limit=50`
    becomes `limit=50`. A query string that survives intact is left byte-identical.
    """
    pairs = parse_qsl(query_string, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if k in _ALLOWED_QUERY_PARAMS]
    if len(kept) == len(pairs):
        return query_string
    return urlencode(kept)


def resolve_environment() -> str:
    """The Sentry `environment` tag.

    An explicit ENVIRONMENT wins; otherwise Railway's own injected environment name is
    used, so a deployment is tagged correctly with nothing to configure. Falls back to
    "development", which is what a local run or a test process gets.
    """
    return (
        settings.ENVIRONMENT
        or os.getenv("RAILWAY_ENVIRONMENT_NAME")
        or os.getenv("RAILWAY_ENVIRONMENT")
        or "development"
    )


# Paths excluded from tracing. `/health` answers every uptime-monitor ping and does no
# work: at a one-minute interval that is ~43,000 empty transactions a month, plausibly
# more than real traffic, and it does not stop when nobody is using the app. Sampling
# every real request is only affordable because this one is sampled at zero.
_UNTRACED_PATHS = frozenset({"/health"})


def _traces_sampler(sampling_context: dict[str, Any]) -> float:
    """Trace every real request, and none of the monitoring pings.

    The ASGI integration puts the raw scope in the sampling context under
    ``asgi_scope``; a non-HTTP transaction has none, and is traced.
    """
    asgi_scope = sampling_context.get("asgi_scope") or {}
    return 0.0 if asgi_scope.get("path") in _UNTRACED_PATHS else 1.0


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    request = event.get("request") or {}

    headers = request.get("headers")
    if isinstance(headers, dict):
        for key in list(headers):
            if key.lower() in _EXTRA_SENSITIVE_HEADERS:
                headers[key] = "[Filtered]"

    query_string = request.get("query_string")
    if isinstance(query_string, str) and query_string:
        request["query_string"] = _filter_query_string(query_string)

    return event


def init_sentry() -> None:
    """Initialise Sentry once, at import time. No DSN configured -> does nothing.

    Must be called *before* the FastAPI app is constructed — see the note in
    ``app/main.py``.
    """
    if not settings.SENTRY_DSN:
        return

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=resolve_environment(),
        # Railway injects the deployed commit SHA. Matching it to the Sentry release is
        # what makes "which deploy introduced this" answerable.
        release=os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
        # Every real request is traced — traffic is low enough that sampling would
        # mostly cost the ability to look up one specific slow request. `/health` is
        # excluded; see `_traces_sampler`. Revisit against the Sentry usage graph, and
        # keep a spend cap set so an overrun is a decision rather than a surprise.
        traces_sampler=_traces_sampler,
        # No profiling: it samples the interpreter itself, which is a different order of
        # overhead and answers a question this app is not asking yet.
        profiles_sample_rate=0.0,
        # No PII: no email, no name, no IP, no request bodies. The only user data sent
        # is the Firebase UID, set explicitly in `get_current_owner`.
        send_default_pii=False,
        max_request_body_size="never",
        before_send=_before_send,
        integrations=[
            # `failed_request_status_codes=set()` disables "any 5xx response is an
            # event". This app raises HTTPException(502/503) deliberately for known,
            # already-handled upstream conditions (agent disabled, extraction disabled,
            # Google token service down) — those are not bugs. Genuinely unhandled
            # exceptions still reach Sentry via the ASGI middleware, and the handful of
            # places that convert a real exception into an HTTPException call
            # capture_exception() explicitly first.
            StarletteIntegration(failed_request_status_codes=set()),
            FastApiIntegration(failed_request_status_codes=set()),
            # Keep log records as breadcrumbs, but never let a logger.error /
            # logger.exception create an event on its own: internal.py:_record logs AND
            # re-raises, which would otherwise report the same failure twice.
            LoggingIntegration(level=logging.INFO, event_level=None),
        ],
    )
    logger.info("Sentry initialised (environment=%s)", resolve_environment())

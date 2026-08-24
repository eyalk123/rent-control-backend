"""Sentry error monitoring. Errors only — no performance tracing, no profiling.

A no-op when ``SENTRY_DSN`` is empty, which is the case in every test run and in local
development, so importing this module is always safe (``tests/conftest.py`` imports
``app.main``, which initialises Sentry at import time).
"""

import logging
import os
from typing import Any

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


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    headers = (event.get("request") or {}).get("headers")
    if isinstance(headers, dict):
        for key in list(headers):
            if key.lower() in _EXTRA_SENSITIVE_HEADERS:
                headers[key] = "[Filtered]"
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
        # Errors only. Tracing stays off deliberately: it keeps the paired web app in
        # the "strictly functional" bucket (no consent banner required) and keeps the
        # event quota for actual errors.
        traces_sample_rate=0.0,
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

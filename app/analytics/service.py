"""Runs the dashboard queries and assembles one JSON payload.

Everything here is read-only and aggregate. Nothing in the returned structure is a renter
name, phone, email, address or any other tenant field — see `queries.py` for the per-query
reasoning, and `tests/test_admin_analytics.py::test_payload_contains_no_pii` for the check
that keeps it true.

The response is cached in memory for CACHE_TTL_SECONDS so that refreshing the page does not
re-run every query. In-process on purpose: the brief rules out adding Redis or any other
service, and a per-worker cache of a read-only aggregate is harmless — the worst case is two
workers briefly disagreeing by less than a minute.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.analytics import queries as Q
from app.clock import utc_now_naive
from app.config import settings

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60

# The ranges the page's picker offers. "all" still needs a concrete lower bound for the
# queries; the product did not exist before this.
RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90}
EPOCH_START = datetime(2025, 1, 1)

_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _jsonable(value: Any) -> Any:
    """Decimals and dates do not survive JSON; convert rather than let FastAPI guess."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _rows(db: Session, sql: str, params: dict) -> list[dict]:
    result = db.execute(text(sql), params)
    keys = list(result.keys())
    return [{k: _jsonable(v) for k, v in zip(keys, row)} for row in result.fetchall()]


def _one(db: Session, sql: str, params: dict) -> dict:
    rows = _rows(db, sql, params)
    return rows[0] if rows else {}


def resolve_window(range_key: str, granularity: str) -> tuple[datetime, datetime, str]:
    """Turn the page's controls into a concrete window. Unknown values fall back rather than
    error — this is a dashboard, and a bad query string should not blank the page."""
    until = utc_now_naive() + timedelta(days=1)  # inclusive of today
    if range_key in RANGE_DAYS:
        since = utc_now_naive() - timedelta(days=RANGE_DAYS[range_key])
    else:
        range_key = "all"
        since = EPOCH_START
    if granularity not in ("day", "week"):
        granularity = "week"
    return since, until, granularity


def _funnel_check(funnel: dict) -> list[str]:
    """The brief's §7 sanity check, enforced in code rather than by eye.

    Each cumulative step must be <= the one before it. It is monotonic by construction (each
    step's filter is a superset of the previous step's conditions), so a violation means the
    query was edited into incorrectness — most likely a join that multiplied rows. Surfacing
    it in the payload means a wrong funnel announces itself instead of being believed.
    """
    steps = [
        ("signed up", funnel.get("step_signed_up")),
        ("added a property", funnel.get("step_property")),
        ("added a renter", funnel.get("step_renter")),
        ("recorded a transaction", funnel.get("step_transaction")),
        ("exported a report", funnel.get("step_export")),
    ]
    warnings = []
    for (prev_label, prev), (label, current) in zip(steps, steps[1:]):
        if prev is None or current is None:
            continue
        if current > prev:
            warnings.append(
                f"Funnel is not monotonic: '{label}' ({current}) exceeds "
                f"'{prev_label}' ({prev}). The join is wrong — do not trust this chart."
            )
    return warnings


def _bucket_histogram(rows: list[dict]) -> dict[str, list]:
    """Reshape the two-metric UNION into chart-ready series, in bucket order rather than the
    lexical order SQL returns ('11+' sorts before '2')."""
    out: dict[str, dict[str, int]] = {"properties": {}, "renters": {}}
    for row in rows:
        metric = row.get("metric")
        if metric in out:
            out[metric][row["bucket"]] = row["owners"]
    return {
        "buckets": Q.BUCKET_ORDER,
        "properties": [out["properties"].get(b, 0) for b in Q.BUCKET_ORDER],
        "renters": [out["renters"].get(b, 0) for b in Q.BUCKET_ORDER],
    }


def build_payload(db: Session, range_key: str, granularity: str, country: str | None) -> dict:
    since, until, granularity = resolve_window(range_key, granularity)
    country = (country or "").strip().upper() or None

    p = {
        "since": since,
        "until": until,
        "granularity": granularity,
        "country": country,
        "daily_message_limit": settings.AGENT_DAILY_MESSAGE_LIMIT,
    }
    # Queries that take no country/granularity still need the binds they do use.
    simple = {"since": since, "until": until}

    funnel = _one(db, Q.FUNNEL, p)
    warnings = _funnel_check(funnel)

    totals = _one(db, Q.OWNER_TOTALS, p)
    if country and (totals.get("deleted_all_time") or 0) > 0:
        warnings.append(
            "Account deletions are not filtered by country: deleted_accounts is an "
            "anonymous tombstone and stores no country."
        )

    payload = {
        "generated_at": utc_now_naive().isoformat(),
        "window": {
            "range": range_key if range_key in RANGE_DAYS else "all",
            "since": since.isoformat(),
            "until": until.isoformat(),
            "granularity": granularity,
            "country": country,
            "cohort_floor": Q.DATA_FLOOR,
        },
        "growth": {
            "totals": totals,
            "signups": _rows(db, Q.SIGNUPS_OVER_TIME, p),
            "deletions": _rows(db, Q.DELETIONS_OVER_TIME, simple),
        },
        "funnel": funnel,
        "time_to_first": _one(db, Q.TIME_TO_FIRST, p),
        "engagement": {
            "active_owners": _rows(db, Q.ACTIVE_OWNERS_OVER_TIME, p),
            "retention": _rows(db, Q.RETENTION_COHORTS, p),
            "distribution": _bucket_histogram(_rows(db, Q.PER_OWNER_DISTRIBUTION, p)),
        },
        "ai": {
            "scans": _rows(db, Q.LEASE_SCANS_OVER_TIME, p),
            "accuracy": _one(db, Q.EXTRACTION_ACCURACY, p),
            "corrected_fields": _rows(db, Q.CORRECTED_FIELDS_BY_NAME, p),
            "assistant": _rows(db, Q.ASSISTANT_USAGE_OVER_TIME, p),
            "cap_hits": _one(db, Q.ASSISTANT_CAP_HITS, p),
            "global_spend": _rows(db, Q.ASSISTANT_GLOBAL_SPEND, simple),
            "limits": {
                "daily_message_limit": settings.AGENT_DAILY_MESSAGE_LIMIT,
                "global_daily_cost_limit_usd": settings.AGENT_GLOBAL_DAILY_COST_LIMIT_USD,
            },
        },
        "platform": {
            "devices": _rows(db, Q.DEVICE_PLATFORM_SPLIT, p),
            "client_usage": _rows(db, Q.CLIENT_USAGE_SPLIT, p),
            "client_over_time": _rows(db, Q.CLIENT_USAGE_OVER_TIME, p),
            "client_overlap": _rows(db, Q.CLIENT_OVERLAP, p),
            "signup_platform": _rows(db, Q.SIGNUP_PLATFORM_SPLIT, p),
            "language": _rows(db, Q.LANGUAGE_SPLIT, p),
            "owner_country": _rows(db, Q.COUNTRY_SPLIT, p),
            "property_country": _rows(db, Q.PROPERTY_COUNTRY_SPLIT, p),
        },
        "operations": {
            "jobs": _rows(db, Q.JOB_HEALTH, {"since": since}),
            "data_health": _rows(db, Q.DATA_HEALTH, {}),
        },
        "warnings": warnings,
        "caveats": _caveats(),
    }
    return payload


def _caveats() -> list[dict]:
    """Shipped with the data, not written on the page, so a number can never be read without
    the reason it might mislead. Each one is a real, known limitation — see
    ANALYTICS_FEASIBILITY.md for the evidence behind them."""
    return [
        {
            "scope": "funnel",
            "text": (
                f"Cohort metrics start {Q.DATA_FLOOR}. Migration 031 backfilled "
                "properties.created_at and renters.created_at to 2026-07-02, and the owners "
                "table only exists from 2026-07-01, so anything earlier is an artefact."
            ),
        },
        {
            "scope": "funnel",
            "text": (
                "Survivor bias: deleting an account erases its properties, renters, "
                "transactions and exports, so the funnel only measures owners who stayed — "
                "who are by definition the ones who activated."
            ),
        },
        {
            "scope": "engagement",
            "text": (
                "'Active' means at least one append-only write (a property, renter, "
                "transaction, export, lease scan, assistant message or deletion). Owners "
                "whose week was purely edits are missed, because updated_at is overwritten "
                "in place."
            ),
        },
        {
            "scope": "ai",
            "text": (
                "Scan abandonment is an upper bound: the outcome is reported by a "
                "client-driven PATCH, so a save whose callback never arrived reads as "
                "abandoned."
            ),
        },
        {
            "scope": "ai",
            "text": (
                "Assistant cap hits are an estimate. A rejected request deletes its own "
                "usage row, so a 429 leaves no trace; this counts owners who reached the "
                "daily limit, not owners who were turned away."
            ),
        },
        {
            "scope": "platform",
            "text": (
                "Client split is measured in writes, not sessions: a five-second app open "
                "and an evening of work both produce one row, and only the counters "
                "separate them. Owner counts per client sum to more than 100% because an "
                "owner can use both."
            ),
        },
        {
            "scope": "platform",
            "text": (
                f"owners.country was added {Q.COUNTRY_KNOWN_FROM} and backfilled to 'IL'. "
                "Owners created before then are reported as 'unknown' rather than Israel."
            ),
        },
    ]


def get_analytics(db: Session, range_key: str, granularity: str, country: str | None) -> dict:
    """Cached entry point. The key includes every control, so switching the date range or
    country is never served a stale answer for a different question."""
    key = (range_key, granularity, country)
    now = time.monotonic()

    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_SECONDS:
            cached = dict(hit[1])
            cached["cached"] = True
            return cached

    started = time.monotonic()
    payload = build_payload(db, range_key, granularity, country)
    payload["query_ms"] = round((time.monotonic() - started) * 1000)
    payload["cached"] = False

    with _cache_lock:
        _cache[key] = (now, payload)
        # The key space is small (4 ranges x 2 granularities x a handful of countries), but
        # an unbounded dict in a long-lived process is still a leak.
        if len(_cache) > 64:
            oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[:32]
            for k, _ in oldest:
                _cache.pop(k, None)

    return payload


def clear_cache() -> None:
    """Used by the tests; also handy if a query is changed without a redeploy."""
    with _cache_lock:
        _cache.clear()

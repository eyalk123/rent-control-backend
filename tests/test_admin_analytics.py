"""Tests for the internal analytics dashboard.

**Scope note, so nobody reads more assurance into this file than it carries.** The suite runs
on in-memory SQLite; the analytics SQL is PostgreSQL-specific (``date_trunc``,
``percentile_cont``, ``DISTINCT ON``, ``jsonb_array_elements``, ``bool_or``) and cannot
execute here. So these tests cover:

* the access-control layer, in full — that is the part where a mistake is a data breach;
* the rate limiter;
* every check on the queries that can be made *without* a database: that each statement's
  named binds are exactly what the service supplies, and that no query mentions a PII column;
* the funnel monotonicity guard, which is pure Python.

What they do **not** cover is whether each query returns the right number. That needs a
PostgreSQL instance with data, and is verified through the dashboard's own Data Health panel
after deployment. See ANALYTICS_FEASIBILITY.md §0.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.analytics import queries as Q
from app.analytics import service as analytics_service
from app.api.routers import admin
from app.config import settings
from app.main import app

ADMIN_UID = "admin-uid-1"
OTHER_UID = "not-an-admin"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

ALL_QUERIES = {
    name: value
    for name, value in vars(Q).items()
    if name.isupper() and isinstance(value, str) and ("SELECT" in value or "WITH" in value)
}


@pytest.fixture(autouse=True)
def _reset_state():
    """Both the analytics cache and the rate limiter are process-global; a test that left
    either populated would silently change the next one's result."""
    analytics_service.clear_cache()
    admin._hits.clear()
    original = settings.ADMIN_OWNER_IDS
    yield
    settings.ADMIN_OWNER_IDS = original
    analytics_service.clear_cache()
    admin._hits.clear()


@pytest.fixture
def anon_client():
    """A client with no auth overrides — the admin routes do their own token check, so the
    suite's usual authenticated client would not exercise them."""
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# Access control — every failure must be indistinguishable from "no such route"
# ─────────────────────────────────────────────────────────────────────────────

def test_analytics_without_token_is_404(anon_client):
    settings.ADMIN_OWNER_IDS = ADMIN_UID
    assert anon_client.get("/admin/analytics").status_code == 404


def test_analytics_with_garbage_token_is_404(anon_client):
    settings.ADMIN_OWNER_IDS = ADMIN_UID
    res = anon_client.get("/admin/analytics", headers={"Authorization": "Bearer not-a-jwt"})
    assert res.status_code == 404


def test_analytics_with_non_admin_token_is_404(anon_client, monkeypatch):
    """A *valid* token for a real user who is not on the allowlist. This is the case a 403
    would leak: it would tell the holder of any working account that the route exists and
    that only authorisation stands in the way."""
    settings.ADMIN_OWNER_IDS = ADMIN_UID
    monkeypatch.setattr(
        admin.id_token, "verify_firebase_token", lambda *a, **k: {"sub": OTHER_UID}
    )
    res = anon_client.get("/admin/analytics", headers={"Authorization": "Bearer valid"})
    assert res.status_code == 404


def test_empty_allowlist_denies_everyone(anon_client, monkeypatch):
    """An unset ADMIN_OWNER_IDS must fail closed. The opposite bug — empty meaning
    'no restriction' — would publish the whole user base's metrics."""
    settings.ADMIN_OWNER_IDS = ""
    monkeypatch.setattr(
        admin.id_token, "verify_firebase_token", lambda *a, **k: {"sub": ADMIN_UID}
    )
    res = anon_client.get("/admin/analytics", headers={"Authorization": "Bearer valid"})
    assert res.status_code == 404


def test_token_in_query_string_is_not_accepted(anon_client, monkeypatch):
    """Query strings leak into access logs, proxies, history and Referer. Only the header
    is honoured, so this must still 404."""
    settings.ADMIN_OWNER_IDS = ADMIN_UID
    monkeypatch.setattr(
        admin.id_token, "verify_firebase_token", lambda *a, **k: {"sub": ADMIN_UID}
    )
    res = anon_client.get("/admin/analytics?token=valid&access_token=valid")
    assert res.status_code == 404


def test_admin_uid_is_accepted(monkeypatch, db_session):
    """The positive case: the gate lets the right uid through.

    The queries themselves cannot run here — they are PostgreSQL and the suite is SQLite — so
    what this asserts is that the request gets *past authorisation* and dies in the query
    layer instead. `raise_server_exceptions=False` turns that into a 500 response rather than
    re-raising, which is the distinction being tested: 500 means "you were let in", 404 means
    "you were not".
    """
    from app.database import get_db

    settings.ADMIN_OWNER_IDS = f"{ADMIN_UID}, someone-else"
    app.dependency_overrides[get_db] = lambda: db_session
    monkeypatch.setattr(
        admin.id_token, "verify_firebase_token", lambda *a, **k: {"sub": ADMIN_UID}
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        res = client.get("/admin/analytics", headers={"Authorization": "Bearer valid"})
    finally:
        app.dependency_overrides.clear()
    assert res.status_code != 404, "admin uid on the allowlist must pass the gate"


# ─────────────────────────────────────────────────────────────────────────────
# The HTML shell
# ─────────────────────────────────────────────────────────────────────────────

def test_dashboard_shell_is_public_but_carries_no_data(anon_client):
    res = anon_client.get("/admin/dashboard")
    assert res.status_code == 200
    assert res.headers["x-robots-tag"].startswith("noindex")
    assert res.headers["cache-control"] == "no-store"
    body = res.text
    assert "login-form" in body
    # The shell must ship an empty frame, never numbers.
    assert "__FIREBASE_CONFIG__" not in body, "config placeholder was not substituted"
    for marker in ("live_owners", "step_signed_up", "owner_client_days"):
        assert f'"{marker}":' not in body, "dashboard shell must not embed analytics data"


# ─────────────────────────────────────────────────────────────────────────────
# Rate limiting
# ─────────────────────────────────────────────────────────────────────────────

def test_rate_limit_eventually_429s(anon_client, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_RATE_LIMIT_PER_MINUTE", 5)
    codes = [anon_client.get("/admin/dashboard").status_code for _ in range(8)]
    assert 429 in codes
    assert codes.index(429) == 5, "limit should allow exactly N before refusing"


# ─────────────────────────────────────────────────────────────────────────────
# Queries: what can be checked without a database
# ─────────────────────────────────────────────────────────────────────────────

# Everything the service passes. A query binding anything outside this set raises at runtime
# the first time someone opens that section of the dashboard.
SUPPLIED_BINDS = {"since", "until", "granularity", "country", "daily_message_limit"}


@pytest.mark.parametrize("name", sorted(ALL_QUERIES))
def test_query_binds_are_all_supplied(name):
    sql = ALL_QUERIES[name]
    # ':name' but not '::type' — Postgres casts are not binds.
    binds = set(re.findall(r"(?<!:):([a-z_][a-z0-9_]*)", sql))
    unknown = binds - SUPPLIED_BINDS
    assert not unknown, f"{name} binds {unknown}, which the service never supplies"


@pytest.mark.parametrize("name", sorted(ALL_QUERIES))
def test_query_parens_balanced(name):
    sql = ALL_QUERIES[name]
    assert sql.count("(") == sql.count(")"), f"{name} has unbalanced parentheses"


# The brief's hard rule: aggregates only. This is a blunt instrument — it greps the SQL for
# column names that carry tenant data — but it is exactly the check that would have caught
# somebody adding `prefilled_value` to the corrected-fields query for debugging and leaving
# it there. `field_edits` is allowed because that query casts the column to expand it, while
# projecting only 'section' and 'field'; the values are asserted absent separately below.
PII_COLUMNS = [
    "first_name", "last_name", "phone", "display_name", "picture_url",
    "prefilled_value", "submitted_value", "source_text", "renter_name",
    "property_address", "inventory_notes", "contact_id", "extra_contacts",
]


@pytest.mark.parametrize("name", sorted(ALL_QUERIES))
def test_no_query_selects_pii(name):
    sql = ALL_QUERIES[name].lower()
    for column in PII_COLUMNS:
        assert column not in sql, f"{name} references PII column '{column}'"


def test_corrected_fields_query_projects_names_not_values():
    """The single most dangerous query in the file: field_edits holds the before/after values
    of corrected lease fields, which are renter names, phone numbers and addresses."""
    sql = Q.CORRECTED_FIELDS_BY_NAME
    assert "->> 'field'" in sql and "->> 'section'" in sql
    for forbidden in ("prefilled_value", "submitted_value", "source_text"):
        assert forbidden not in sql


def test_address_only_appears_as_a_forbidden_word_nowhere():
    """`address` is a property column and must not be selected anywhere. Checked separately
    from PII_COLUMNS because the substring appears in unrelated words like 'addressed'."""
    for name, sql in ALL_QUERIES.items():
        assert not re.search(r"\baddress\b", sql, re.I), f"{name} references address"


def test_cohort_queries_are_floored():
    """The funnel, time-to-first and retention queries must all clamp to DATA_FLOOR, or they
    report the 2026-07-02 backfill as real activation."""
    for name in ("FUNNEL", "TIME_TO_FIRST", "RETENTION_COHORTS"):
        assert Q.DATA_FLOOR in ALL_QUERIES[name], f"{name} is not floored at DATA_FLOOR"


def test_every_windowed_query_filters_on_time():
    """The brief requires a bounded window on every query. The three exempt ones aggregate
    whole small tables by design."""
    unbounded = {"DATA_HEALTH", "PER_OWNER_DISTRIBUTION", "DEVICE_PLATFORM_SPLIT",
                 "SIGNUP_PLATFORM_SPLIT", "LANGUAGE_SPLIT", "COUNTRY_SPLIT",
                 "PROPERTY_COUNTRY_SPLIT", "OWNER_TOTALS", "SCOPED_OWNERS",
                 "WRITE_EVENTS", "COUNTRY_EXPR"}
    for name, sql in ALL_QUERIES.items():
        if name in unbounded:
            continue
        assert ":since" in sql, f"{name} has no time bound"


# ─────────────────────────────────────────────────────────────────────────────
# Funnel monotonicity guard
# ─────────────────────────────────────────────────────────────────────────────

def test_funnel_check_passes_on_a_descending_funnel():
    assert analytics_service._funnel_check({
        "step_signed_up": 100, "step_property": 60, "step_renter": 40,
        "step_transaction": 25, "step_export": 5,
    }) == []


def test_funnel_check_flags_a_step_that_grows():
    warnings = analytics_service._funnel_check({
        "step_signed_up": 100, "step_property": 60, "step_renter": 75,
        "step_transaction": 25, "step_export": 5,
    })
    assert len(warnings) == 1
    assert "not monotonic" in warnings[0]
    assert "added a renter" in warnings[0]


def test_funnel_check_tolerates_missing_steps():
    """An empty database returns NULLs, which must read as 'nothing to check', not a crash."""
    assert analytics_service._funnel_check({"step_signed_up": None}) == []


def test_funnel_check_allows_equal_steps():
    assert analytics_service._funnel_check({
        "step_signed_up": 10, "step_property": 10, "step_renter": 10,
        "step_transaction": 10, "step_export": 10,
    }) == []


# ─────────────────────────────────────────────────────────────────────────────
# Window resolution
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key,expected", [("7d", "7d"), ("30d", "30d"), ("90d", "90d")])
def test_known_ranges_resolve(key, expected):
    since, until, _ = analytics_service.resolve_window(key, "week")
    assert (until - since).days >= analytics_service.RANGE_DAYS[expected]


def test_unknown_range_falls_back_rather_than_raising():
    """A bad query string should not blank the dashboard."""
    since, _, gran = analytics_service.resolve_window("nonsense", "nonsense")
    assert since == analytics_service.EPOCH_START
    assert gran == "week"

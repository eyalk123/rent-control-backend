"""Who is behind a number: the owners inside one bar, point, slice or cell of the dashboard.

**This is the one file in the analytics package that returns identity.** `queries.py` stays
aggregate-only, and its PII test keeps it that way. Everything that names a person lives here,
so the boundary is a file boundary and can be reviewed as one.

What it returns, and what it never does:

* **Owners only** — the landlord accounts. Name, email, country, signup date, signup platform,
  plan, property count, last activity and last seen. These are *our* users' account details, which the
  admin already sees in the database and in Firebase.
* **Never a tenant field.** A segment may *filter* on renters, transactions or lease scans
  ("owners who added a renter"), but the SELECT reads from `owners` and per-owner counts only.
  `tests/test_admin_analytics.py` greps this file for renter and lease-content columns.

Every segment mirrors the aggregate query that draws its chart, so the list is the same people
the number counted. Where it cannot be exact, the docstring on the segment says why.

## How a request maps to SQL

Each segment is the body of a `seg` CTE that yields owner ids. `_statement` wraps it with the
shared CTEs and the one SELECT that every list uses, so there is exactly one table shape.

Binds, all named, all supplied on every call (unused ones are harmless):

* `:since` / `:until` / `:country` — the page's window and filter, exactly as the dashboard.
* `:lo` / `:hi` — the slice of time the click selected: the bucket clipped to the window, or
  the whole window when the chart has no time axis.
* `:value` / `:group` — what was clicked (a funnel step, a country, a plan…) and, for
  charts with a switch, which metric it was showing.
* `:exclude` — comma-separated country codes, for the "Other" slice and line.
* `:weeks`, `:min_n`, `:max_n`, `:daily_message_limit`, `:limit` — segment-specific numbers.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.analytics import queries as Q

# Rows returned per click. `total` always reports the full count, so a cut-off list says so.
ROW_LIMIT = 500

# Retention cohorts are weekly whatever the page's granularity.
_FIXED_SPAN = {"retention": "week"}

_FLOOR = f"TIMESTAMP '{Q.DATA_FLOOR}'"

_PAYING_STATES = "('active', 'canceling', 'payment_failed')"

# The state of one subscription row — SUBSCRIPTION_STATE's CASE, restated.
_SUB_STATE = f"""
    sub_state AS (
        SELECT sub.owner_id,
               CASE
                   WHEN NOT {Q._ENTITLED}                   THEN 'ended'
                   WHEN lower(sub.status) = 'trialing'      THEN 'trial'
                   WHEN lower(sub.status) = 'past_due'      THEN 'payment_failed'
                   WHEN lower(sub.status) = 'canceled'      THEN 'canceling'
                   ELSE 'active'
               END                AS state,
               lower(sub.plan)    AS plan,
               sub.source         AS source,
               sub.price_currency AS currency
        FROM subscriptions sub
        JOIN scoped_owners s ON s.id = sub.owner_id
    )
"""

# `group` picks the column, `value` is what was clicked. No group = every row in scope.
_SUB_MATCH = """
    (CAST(:group AS text) IS NULL
     OR CASE CAST(:group AS text)
            WHEN 'state'    THEN st.state
            WHEN 'plan'     THEN st.plan
            WHEN 'source'   THEN st.source
            WHEN 'currency' THEN st.currency
        END = CAST(:value AS text))
"""


SEGMENTS: dict[str, str] = {
    # Every live account in scope. "Live owners" tiles.
    "all_owners": """
        SELECT s.id FROM scoped_owners s
    """,

    # SIGNUPS_OVER_TIME, one bucket or the whole window.
    "signups": """
        SELECT s.id FROM scoped_owners s
        WHERE s.created_at >= :lo AND s.created_at < :hi
    """,

    # FUNNEL's cumulative steps: step N requires every step before it. `value` is the
    # step's key in the funnel payload (step_signed_up … step_export).
    "funnel": f"""
        SELECT s.id FROM scoped_owners s
        WHERE s.created_at >= GREATEST(:lo, {_FLOOR}) AND s.created_at < :hi
          AND CAST(:value AS text) IN ('step_signed_up', 'step_property', 'step_renter',
                                       'step_transaction', 'step_export')
          AND (CAST(:value AS text) = 'step_signed_up'
               OR EXISTS (SELECT 1 FROM properties x WHERE x.owner_id = s.id))
          AND (CAST(:value AS text) IN ('step_signed_up', 'step_property')
               OR EXISTS (SELECT 1 FROM renters x WHERE x.owner_id = s.id))
          AND (CAST(:value AS text) IN ('step_signed_up', 'step_property', 'step_renter')
               OR EXISTS (SELECT 1 FROM transactions x WHERE x.owner_id = s.id))
          AND (CAST(:value AS text) <> 'step_export'
               OR EXISTS (SELECT 1 FROM report_exports x WHERE x.owner_id = s.id))
    """,

    # ACTIVE_OWNERS_OVER_TIME: at least one append-only write in the slice.
    "active": """
        SELECT DISTINCT e.owner_id AS id
        FROM write_events e
        JOIN scoped_owners s ON s.id = e.owner_id
        WHERE e.created_at >= :lo AND e.created_at < :hi
    """,

    # LAST_SEEN_DAYS, one bar. `value` is the bar (0–9, "10" = 10–29, "30" = 30+); `group`
    # "paying" / "not_paying" is the stacked half, NULL the whole bar.
    "last_seen": f"""
        SELECT s.id FROM scoped_owners s
        JOIN owners o ON o.id = s.id
        {Q._SEEN_DAYS}
        WHERE {Q.DAYS_SINCE_SEEN} = CAST(:value AS int)
          AND (CAST(:group AS text) IS NULL
               OR (CAST(:group AS text) = 'paying') = {Q.PAYING_OWNER})
    """,

    # RETENTION_COHORTS, one cell. `weeks` NULL = the whole cohort (the Size column);
    # otherwise owners with a write in days [7N, 7N+7) after their own signup.
    "retention": f"""
        SELECT s.id FROM scoped_owners s
        WHERE s.created_at >= GREATEST(:lo, {_FLOOR}) AND s.created_at < :hi
          AND (CAST(:weeks AS int) IS NULL OR EXISTS (
                SELECT 1 FROM write_events e
                WHERE e.owner_id = s.id
                  AND e.created_at >= s.created_at + CAST(:weeks AS int) * INTERVAL '7 days'
                  AND e.created_at <  s.created_at + (CAST(:weeks AS int) + 1) * INTERVAL '7 days'))
    """,

    # PER_OWNER_DISTRIBUTION, one property band.
    "property_band": """
        SELECT s.id FROM scoped_owners s
        LEFT JOIN (SELECT owner_id, count(*) AS n FROM properties GROUP BY owner_id) p
               ON p.owner_id = s.id
        WHERE COALESCE(p.n, 0) >= CAST(:min_n AS int)
          AND (CAST(:max_n AS int) IS NULL OR COALESCE(p.n, 0) <= CAST(:max_n AS int))
    """,

    # ACCOUNT_PLANS: every account sorted into exactly one of paid / grandfathered / free.
    "account_plan": f"""
        SELECT s.id FROM scoped_owners s
        JOIN owners o ON o.id = s.id
        LEFT JOIN (SELECT DISTINCT sub.owner_id FROM subscriptions sub WHERE {Q._ENTITLED}) e
               ON e.owner_id = s.id
        LEFT JOIN (SELECT owner_id, count(*) AS n FROM properties GROUP BY owner_id) p
               ON p.owner_id = s.id
        WHERE CASE CAST(:value AS text)
                  WHEN 'paid'          THEN e.owner_id IS NOT NULL
                  WHEN 'grandfathered' THEN e.owner_id IS NULL
                                            AND COALESCE(lower(o.granted_plan), 'free') <> 'free'
                  WHEN 'free'          THEN e.owner_id IS NULL
                                            AND COALESCE(lower(o.granted_plan), 'free') = 'free'
                  WHEN 'free_at_limit' THEN e.owner_id IS NULL
                                            AND COALESCE(lower(o.granted_plan), 'free') = 'free'
                                            AND COALESCE(p.n, 0) >= 2
                  ELSE false
              END
    """,

    # Subscribers counted as paying (trials excluded), optionally one plan/store/currency.
    "subs_paying": f"""
        SELECT st.owner_id AS id FROM sub_state st
        WHERE st.state IN {_PAYING_STATES} AND {_SUB_MATCH}
    """,

    # Every subscription row, any state — the "By status" bars.
    "subs_any": f"""
        SELECT st.owner_id AS id FROM sub_state st
        WHERE {_SUB_MATCH}
    """,

    # SUBSCRIPTION_EVENTS_OVER_TIME, one bar.
    "subs_events": """
        SELECT e.owner_id AS id
        FROM subscription_events e
        JOIN scoped_owners s ON s.id = e.owner_id
        WHERE e.occurred_at >= :lo AND e.occurred_at < :hi
          AND COALESCE(upper(e.environment), '') <> 'SANDBOX'
          AND e.event_type = CASE CAST(:value AS text)
                                 WHEN 'started'  THEN 'INITIAL_PURCHASE'
                                 WHEN 'canceled' THEN 'CANCELLATION'
                             END
    """,

    # The Countries tab. Ignores the page's country filter, like COUNTRY_COMPARISON.
    # `value` is one country, `exclude` is the countries drawn on their own (for "Other"),
    # `group` is the metric the chart was showing.
    "country": f"""
        SELECT c.id FROM country_owners c
        WHERE (CAST(:value AS text) IS NULL OR c.country = CAST(:value AS text))
          AND (CAST(:exclude AS text) IS NULL
               OR NOT (c.country = ANY (string_to_array(CAST(:exclude AS text), ','))))
          AND CASE CAST(:group AS text)
                  WHEN 'owners'        THEN true
                  WHEN 'signups'       THEN c.created_at >= :lo AND c.created_at < :hi
                  WHEN 'active_owners' THEN EXISTS (
                      SELECT 1 FROM write_events e
                      WHERE e.owner_id = c.id AND e.created_at >= :lo AND e.created_at < :hi)
                  WHEN 'properties'    THEN EXISTS (
                      SELECT 1 FROM properties p WHERE p.owner_id = c.id)
                  WHEN 'scans'         THEN EXISTS (
                      SELECT 1 FROM document_extraction_logs l
                      WHERE l.owner_id = c.id AND l.created_at >= :lo AND l.created_at < :hi)
                  WHEN 'messages'      THEN EXISTS (
                      SELECT 1 FROM agent_usage_logs u
                      WHERE u.owner_id = c.id AND u.created_at >= :lo AND u.created_at < :hi)
                  WHEN 'paying'        THEN EXISTS (
                      SELECT 1 FROM subscriptions sub
                      WHERE sub.owner_id = c.id AND {Q._ENTITLED})
                  ELSE false
              END
    """,

    # LEASE_SCANS_OVER_TIME: owners with a scan in the slice. `value` 'saved' / 'abandoned'
    # narrows to scans with that outcome; anything else is every scan.
    "scans": """
        SELECT DISTINCT l.owner_id AS id
        FROM document_extraction_logs l
        JOIN scoped_owners s ON s.id = l.owner_id
        WHERE l.created_at >= :lo AND l.created_at < :hi
          AND (CAST(:value AS text) IS DISTINCT FROM 'saved'     OR l.submitted_at IS NOT NULL)
          AND (CAST(:value AS text) IS DISTINCT FROM 'abandoned' OR l.submitted_at IS NULL)
    """,

    # CORRECTED_FIELDS_BY_NAME, one row: owners who corrected this field. 🔴 The JSON is read
    # for `field` and `section` only, to filter — never selected. Same rule as queries.py.
    "corrected_field": """
        SELECT DISTINCT l.owner_id AS id
        FROM document_extraction_logs l
        JOIN scoped_owners s ON s.id = l.owner_id
        WHERE l.created_at >= :lo AND l.created_at < :hi
          AND l.field_edits IS NOT NULL
          AND l.field_edits <> ''
          AND EXISTS (
              SELECT 1 FROM jsonb_array_elements(l.field_edits::jsonb) AS e(value)
              WHERE e.value ->> 'field' = CAST(:value AS text)
                AND (e.value ->> 'section') IS NOT DISTINCT FROM CAST(:group AS text))
    """,

    # ASSISTANT_USAGE_OVER_TIME: owners who sent a message in the slice.
    "assistant": """
        SELECT DISTINCT u.owner_id AS id
        FROM agent_usage_logs u
        JOIN scoped_owners s ON s.id = u.owner_id
        WHERE u.created_at >= :lo AND u.created_at < :hi
    """,

    # ASSISTANT_CAP_HITS: owners with at least one day at the daily limit.
    "assistant_cap": """
        SELECT u.owner_id AS id
        FROM agent_usage_logs u
        JOIN scoped_owners s ON s.id = u.owner_id
        WHERE u.created_at >= :lo AND u.created_at < :hi
        GROUP BY u.owner_id, date_trunc('day', u.created_at)
        HAVING count(*) >= :daily_message_limit
    """,

    # CLIENT_USAGE_OVER_TIME, one bar: owners who wrote from this app in the slice. The chart
    # sums writes, so an owner who only opened the app adds nothing and is left out here.
    "client_app": """
        SELECT DISTINCT c.owner_id AS id
        FROM owner_client_days c
        JOIN scoped_owners s ON s.id = c.owner_id
        WHERE CAST(c.day AS timestamp) >= :lo AND CAST(c.day AS timestamp) < :hi
          AND c.app = CAST(:value AS text)
          AND c.writes > 0
    """,

    # CLIENT_OVERLAP, one row.
    "client_overlap": """
        SELECT po.owner_id AS id FROM (
            SELECT c.owner_id,
                   bool_or(c.app = 'mobile') AS on_mobile,
                   bool_or(c.app = 'web')    AS on_web
            FROM owner_client_days c
            JOIN scoped_owners s ON s.id = c.owner_id
            WHERE c.day >= :since AND c.day < :until
            GROUP BY c.owner_id
        ) po
        WHERE CASE WHEN po.on_mobile AND po.on_web THEN 'both'
                   WHEN po.on_mobile                THEN 'mobile only'
                   WHEN po.on_web                   THEN 'web only'
                   ELSE 'unknown' END = CAST(:value AS text)
    """,

    # LANGUAGE_SPLIT / SIGNUP_PLATFORM_SPLIT: the owner's first consent row. A missing
    # `value` matches the owners whose row has none, which the chart shows as its own bar.
    "language": """
        SELECT fa.owner_id AS id FROM (
            SELECT DISTINCT ON (la.owner_id) la.owner_id, la.locale
            FROM legal_acceptances la
            JOIN scoped_owners s ON s.id = la.owner_id
            ORDER BY la.owner_id, la.accepted_at
        ) fa
        WHERE fa.locale IS NOT DISTINCT FROM CAST(:value AS text)
    """,
    "signup_platform": """
        SELECT fa.owner_id AS id FROM (
            SELECT DISTINCT ON (la.owner_id) la.owner_id, la.platform::text AS platform
            FROM legal_acceptances la
            JOIN scoped_owners s ON s.id = la.owner_id
            ORDER BY la.owner_id, la.accepted_at
        ) fa
        WHERE fa.platform IS NOT DISTINCT FROM CAST(:value AS text)
    """,

    # DEVICE_PLATFORM_SPLIT: owners with a push token on this platform.
    "devices": """
        SELECT DISTINCT d.owner_id AS id
        FROM device_tokens d
        JOIN scoped_owners s ON s.id = d.owner_id
        WHERE d.platform::text = CAST(:value AS text)
    """,
}

# Property bands, as PER_OWNER_DISTRIBUTION draws them: (min, max), max None = open.
PROPERTY_BANDS = {"0": (0, 0), "1-2": (1, 2), "3-8": (3, 8), "9-15": (9, 15), "16+": (16, None)}

RETENTION_WEEKS = {"week_1": 1, "week_4": 4}


def _statement(segment_sql: str) -> str:
    """One table shape for every click. Unused CTEs are never evaluated by Postgres."""
    return f"""
WITH {Q.SCOPED_OWNERS},
{Q._COUNTRY_OWNERS},
{Q.WRITE_EVENTS},
{_SUB_STATE},
seg AS ({segment_sql}),
picked AS (SELECT DISTINCT id FROM seg),
first_acceptance AS (
    SELECT DISTINCT ON (la.owner_id) la.owner_id, la.platform::text AS platform
    FROM legal_acceptances la
    JOIN picked k ON k.id = la.owner_id
    ORDER BY la.owner_id, la.accepted_at
),
paid AS (
    SELECT DISTINCT ON (sub.owner_id) sub.owner_id,
           lower(sub.plan)                  AS plan,
           lower(sub.status) = 'trialing'   AS on_trial
    FROM subscriptions sub
    JOIN picked k ON k.id = sub.owner_id
    WHERE {Q._ENTITLED}
    ORDER BY sub.owner_id, sub.current_period_end DESC NULLS FIRST
),
prop_counts AS (
    SELECT p.owner_id, count(*) AS n
    FROM properties p
    JOIN picked k ON k.id = p.owner_id
    GROUP BY p.owner_id
),
last_write AS (
    SELECT e.owner_id, max(e.created_at) AS t
    FROM write_events e
    JOIN picked k ON k.id = e.owner_id
    GROUP BY e.owner_id
)
SELECT o.id,
       o.display_name                        AS name,
       o.email,
       COALESCE(o.country, 'unknown')        AS country,
       o.created_at                          AS signed_up,
       fa.platform                           AS platform,
       CASE WHEN pd.plan IS NOT NULL                              THEN pd.plan
            WHEN COALESCE(lower(o.granted_plan), 'free') <> 'free' THEN 'grandfathered'
            ELSE 'free' END                  AS plan,
       COALESCE(pd.on_trial, false)          AS on_trial,
       COALESCE(pc.n, 0)                     AS properties,
       lw.t                                  AS last_active,
       COALESCE(o.last_seen_at, o.created_at) AS last_seen,
       count(*) OVER ()                      AS total
FROM picked k
JOIN owners o                  ON o.id = k.id
LEFT JOIN first_acceptance fa  ON fa.owner_id = o.id
LEFT JOIN paid pd              ON pd.owner_id = o.id
LEFT JOIN prop_counts pc       ON pc.owner_id = o.id
LEFT JOIN last_write lw        ON lw.owner_id = o.id
ORDER BY o.created_at DESC NULLS LAST, o.id
LIMIT :limit
"""


STATEMENTS: dict[str, str] = {name: _statement(sql) for name, sql in SEGMENTS.items()}


def parse_bucket(raw: str | None) -> datetime | None:
    """A bucket start as the page sends it: the payload's own naive-UTC ISO string, or the
    browser's `toISOString()` with a trailing Z. Returned naive, like every stored column."""
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def time_slice(
    segment: str, since: datetime, until: datetime, granularity: str, bucket: datetime | None
) -> tuple[datetime, datetime]:
    """The clicked bucket clipped to the window — the first bucket of a range usually starts
    before `since`, and the chart only counted from `since`."""
    if bucket is None:
        return since, until
    span = _FIXED_SPAN.get(segment, granularity)
    end = bucket + (timedelta(days=7) if span == "week" else timedelta(days=1))
    return max(since, bucket), min(until, end)

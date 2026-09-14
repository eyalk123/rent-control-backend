"""Every SQL query behind the admin analytics dashboard, in one reviewable file.

Read this before trusting a number on the page. Each constant is a complete, standalone
statement you can paste into psql and run by hand — that is deliberate, so any figure the
dashboard shows can be checked independently (the brief's §7 cross-check).

Three rules hold everywhere in this file:

1. **Aggregates only.** No query selects a renter name, phone, email, address or any other
   tenant field. Counts, sums, medians, buckets. The one place extracted lease content
   could leak is ``document_extraction_logs.field_edits``, whose JSON holds the *values*
   the user corrected — ``CORRECTED_FIELDS_BY_NAME`` projects ``field`` and ``section``
   only, never ``prefilled_value``, ``submitted_value`` or ``source_text``. Keep it that
   way.
2. **Bounded windows.** Every query takes ``:since`` / ``:until`` and filters on them.
3. **Named binds for everything caller-supplied.** The only Python interpolation is of
   constants defined in this file (the scoped-owners CTE and the country expression),
   never of request input — so there is no injection path through the date range,
   granularity or country filter.

## The two dates you need to know about

``DATA_FLOOR`` — migration 031 added ``created_at`` to ``properties`` and ``renters`` and
backfilled every existing row to the migration timestamp (2026-07-02); the ``owners`` table
itself was only created on 2026-07-01, so an owner who signed up earlier has a ``created_at``
of "whenever they next opened the app". Anything cohort-shaped before 2026-07-06 is therefore
fiction, and every funnel, cohort and time-to-first query here floors at it.

``COUNTRY_KNOWN_FROM`` — ``owners.country`` was added 2026-09-13 and backfilled to ``'IL'``.
Owners created before that date have a country nobody chose, so ``COUNTRY_EXPR`` reports them
as ``'unknown'`` rather than letting a migration default masquerade as a signal.
"""

# Cohort/funnel floor. See the module docstring.
DATA_FLOOR = "2026-07-06"

# Before this, owners.country is a backfill, not a choice.
COUNTRY_KNOWN_FROM = "2026-09-13"

# The honest country for an owner: 'unknown' unless they actually chose one.
COUNTRY_EXPR = f"""
    CASE WHEN o.created_at < TIMESTAMP '{COUNTRY_KNOWN_FROM}' THEN 'unknown'
         ELSE COALESCE(o.country, 'unknown') END
"""

# Owners in scope for a request: the country filter, applied once. `:country` NULL means
# "all countries". Interpolated into the queries below — it is a constant defined here, not
# caller input, so there is no injection path.
#
# The CAST is not decoration. A bare parameter on its own beside IS NULL gives Postgres
# nothing to infer a type from, and it raises "could not determine data type of parameter"
# before the query ever runs. Naming the type fixes it.
SCOPED_OWNERS = f"""
    scoped_owners AS (
        SELECT o.id, o.created_at, {COUNTRY_EXPR} AS country
        FROM owners o
        WHERE (CAST(:country AS text) IS NULL OR {COUNTRY_EXPR} = CAST(:country AS text))
    )
"""

# Every append-only event that means "this owner did something". Used for weekly-active and
# retention. `updated_at` is deliberately absent: it is overwritten in place, so it only ever
# evidences the *most recent* edit and would inflate whichever period is being asked about.
# Deletions are covered because activity_log is append-only. Pure edits are the known gap —
# they become visible once activity_log records 'update' (see ACTIVITY_LOG_EDIT_LOGGING_PROMPT).
WRITE_EVENTS = """
    write_events AS (
        SELECT owner_id, created_at FROM properties
        UNION ALL SELECT owner_id, created_at FROM renters
        UNION ALL SELECT owner_id, created_at FROM transactions
        UNION ALL SELECT owner_id, created_at FROM report_exports
        UNION ALL SELECT owner_id, created_at FROM document_extraction_logs
        UNION ALL SELECT owner_id, created_at FROM agent_usage_logs
        UNION ALL SELECT owner_id, created_at FROM activity_log
    )
"""


# ─────────────────────────────────────────────────────────────────────────────
# Growth
# ─────────────────────────────────────────────────────────────────────────────

SIGNUPS_OVER_TIME = f"""
WITH {SCOPED_OWNERS}
SELECT date_trunc(CAST(:granularity AS text), s.created_at) AS bucket,
       count(*)                               AS signups
FROM scoped_owners s
WHERE s.created_at >= :since AND s.created_at < :until
GROUP BY 1
ORDER BY 1
"""

# Live accounts only — account deletion removes the owners row, so this is "signups that
# still exist", not all-time signups. DELETIONS_OVER_TIME is the other half of the picture.
OWNER_TOTALS = f"""
WITH {SCOPED_OWNERS}
SELECT (SELECT count(*) FROM scoped_owners)                                     AS live_owners,
       (SELECT count(*) FROM scoped_owners WHERE created_at >= :since
                                             AND created_at < :until)           AS new_in_window,
       (SELECT count(*) FROM deleted_accounts)                                  AS deleted_all_time,
       (SELECT count(*) FROM deleted_accounts
         WHERE deleted_at >= :since AND deleted_at < :until)                    AS deleted_in_window
"""

# deleted_accounts is the anonymous tombstone: a hash and three counts, no country. So this
# one cannot honour the country filter — flagged in the payload rather than silently ignored.
DELETIONS_OVER_TIME = """
SELECT date_trunc('month', deleted_at) AS bucket,
       count(*)                        AS deletions,
       sum(properties_count)           AS properties_lost,
       sum(renters_count)              AS renters_lost,
       sum(transactions_count)         AS transactions_lost
FROM deleted_accounts
WHERE deleted_at >= :since AND deleted_at < :until
GROUP BY 1
ORDER BY 1
"""


# ─────────────────────────────────────────────────────────────────────────────
# Activation funnel
# ─────────────────────────────────────────────────────────────────────────────

# Two readings of the same five steps.
#
# `step_*` is CUMULATIVE — step N requires steps 1..N-1 — so it is monotonic by construction
# and is what the funnel chart draws. `ever_*` is the raw "did this owner ever do X", which
# is NOT necessarily monotonic: renters and transactions carry their own owner_id and can
# exist with no property, so an owner can have a renter and no property at all.
#
# Both are returned. Where they disagree, owners are taking the product out of order, which
# is worth seeing rather than hiding behind a tidy funnel.
FUNNEL = f"""
WITH {SCOPED_OWNERS},
first_property AS (SELECT owner_id, min(created_at) AS t FROM properties              GROUP BY owner_id),
first_renter   AS (SELECT owner_id, min(created_at) AS t FROM renters                 GROUP BY owner_id),
first_txn      AS (SELECT owner_id, min(created_at) AS t FROM transactions            GROUP BY owner_id),
first_export   AS (SELECT owner_id, min(created_at) AS t FROM report_exports          GROUP BY owner_id),
cohort AS (
    SELECT s.id, p.t AS t_prop, r.t AS t_renter, x.t AS t_txn, e.t AS t_export
    FROM scoped_owners s
    LEFT JOIN first_property p ON p.owner_id = s.id
    LEFT JOIN first_renter   r ON r.owner_id = s.id
    LEFT JOIN first_txn      x ON x.owner_id = s.id
    LEFT JOIN first_export   e ON e.owner_id = s.id
    WHERE s.created_at >= GREATEST(:since, TIMESTAMP '{DATA_FLOOR}')
      AND s.created_at <  :until
)
SELECT
    count(*)                                                                   AS step_signed_up,
    count(*) FILTER (WHERE t_prop IS NOT NULL)                                 AS step_property,
    count(*) FILTER (WHERE t_prop IS NOT NULL AND t_renter IS NOT NULL)        AS step_renter,
    count(*) FILTER (WHERE t_prop IS NOT NULL AND t_renter IS NOT NULL
                       AND t_txn IS NOT NULL)                                  AS step_transaction,
    count(*) FILTER (WHERE t_prop IS NOT NULL AND t_renter IS NOT NULL
                       AND t_txn IS NOT NULL AND t_export IS NOT NULL)         AS step_export,
    count(*) FILTER (WHERE t_prop   IS NOT NULL)                               AS ever_property,
    count(*) FILTER (WHERE t_renter IS NOT NULL)                               AS ever_renter,
    count(*) FILTER (WHERE t_txn    IS NOT NULL)                               AS ever_transaction,
    count(*) FILTER (WHERE t_export IS NOT NULL)                               AS ever_export
FROM cohort
"""

# Medians only over owners who signed up after DATA_FLOOR. Before it, `owners.created_at` and
# the backfilled `properties.created_at` sit a day apart regardless of the truth, which would
# report "1 day to first property" for an owner who had had one for a year.
TIME_TO_FIRST = f"""
WITH {SCOPED_OWNERS},
first_property AS (SELECT owner_id, min(created_at) AS t FROM properties GROUP BY owner_id),
first_renter   AS (SELECT owner_id, min(created_at) AS t FROM renters    GROUP BY owner_id),
eligible AS (
    SELECT s.id, s.created_at,
           p.t AS t_prop,
           r.t AS t_renter
    FROM scoped_owners s
    LEFT JOIN first_property p ON p.owner_id = s.id
    LEFT JOIN first_renter   r ON r.owner_id = s.id
    WHERE s.created_at >= GREATEST(:since, TIMESTAMP '{DATA_FLOOR}')
      AND s.created_at <  :until
)
SELECT
    percentile_cont(0.5) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (t_prop - created_at)) / 3600.0
    ) FILTER (WHERE t_prop IS NOT NULL AND t_prop >= created_at)      AS median_hours_to_property,
    count(*) FILTER (WHERE t_prop IS NOT NULL AND t_prop >= created_at)   AS sample_property,
    percentile_cont(0.5) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (t_renter - created_at)) / 3600.0
    ) FILTER (WHERE t_renter IS NOT NULL AND t_renter >= created_at)  AS median_hours_to_renter,
    count(*) FILTER (WHERE t_renter IS NOT NULL AND t_renter >= created_at) AS sample_renter,
    count(*)                                                              AS eligible_owners
FROM eligible
"""


# ─────────────────────────────────────────────────────────────────────────────
# Engagement
# ─────────────────────────────────────────────────────────────────────────────

# "Active" == made at least one append-only write in the bucket. Stated on the page, because
# the number means nothing without it. See WRITE_EVENTS for what is and isn't counted.
ACTIVE_OWNERS_OVER_TIME = f"""
WITH {SCOPED_OWNERS},
{WRITE_EVENTS}
SELECT date_trunc(CAST(:granularity AS text), e.created_at) AS bucket,
       count(DISTINCT e.owner_id)             AS active_owners,
       count(*)                               AS write_events
FROM write_events e
JOIN scoped_owners s ON s.id = e.owner_id
WHERE e.created_at >= :since AND e.created_at < :until
GROUP BY 1
ORDER BY 1
"""

# Week-1 and week-4 retention by signup cohort. `cohort_size` is returned so a 2-of-3 cohort
# is never read as "67% retention" — with only a few trustworthy weeks of data, the sample
# size is as important as the percentage.
RETENTION_COHORTS = f"""
WITH {SCOPED_OWNERS},
{WRITE_EVENTS},
cohorts AS (
    SELECT s.id, date_trunc('week', s.created_at) AS cohort_week, s.created_at
    FROM scoped_owners s
    WHERE s.created_at >= GREATEST(:since, TIMESTAMP '{DATA_FLOOR}')
      AND s.created_at <  :until
),
activity AS (
    SELECT DISTINCT c.id, c.cohort_week,
           floor(EXTRACT(EPOCH FROM (e.created_at - c.created_at)) / 604800.0)::int AS week_offset
    FROM cohorts c
    JOIN write_events e ON e.owner_id = c.id
    WHERE e.created_at >= c.created_at
)
SELECT c.cohort_week,
       count(DISTINCT c.id)                                                     AS cohort_size,
       count(DISTINCT a1.id)                                                    AS retained_week_1,
       count(DISTINCT a4.id)                                                    AS retained_week_4
FROM cohorts c
LEFT JOIN activity a1 ON a1.id = c.id AND a1.week_offset = 1
LEFT JOIN activity a4 ON a4.id = c.id AND a4.week_offset = 4
GROUP BY c.cohort_week
ORDER BY c.cohort_week
"""

# Histogram buckets. Owners with zero are included — they are the most interesting bucket in
# a product with no onboarding, and a GROUP BY over the child table alone would drop them.
PER_OWNER_DISTRIBUTION = f"""
WITH {SCOPED_OWNERS},
prop_counts   AS (SELECT owner_id, count(*) AS n FROM properties GROUP BY owner_id),
renter_counts AS (SELECT owner_id, count(*) AS n FROM renters    GROUP BY owner_id),
counts AS (
    SELECT s.id,
           COALESCE(p.n, 0) AS n_properties,
           COALESCE(r.n, 0) AS n_renters
    FROM scoped_owners s
    LEFT JOIN prop_counts   p ON p.owner_id = s.id
    LEFT JOIN renter_counts r ON r.owner_id = s.id
)
SELECT bucket_of(n_properties) AS bucket, count(*) AS owners, 'properties' AS metric
FROM counts GROUP BY 1
UNION ALL
SELECT bucket_of(n_renters), count(*), 'renters'
FROM counts GROUP BY 1
"""

# Inlined rather than a real SQL function so the query stays copy-pasteable into psql with no
# setup. Kept in one place so the two halves of the histogram can never drift apart.
_BUCKET_CASE = """CASE WHEN {col} = 0 THEN '0'
         WHEN {col} = 1 THEN '1'
         WHEN {col} = 2 THEN '2'
         WHEN {col} BETWEEN 3 AND 5  THEN '3-5'
         WHEN {col} BETWEEN 6 AND 10 THEN '6-10'
         ELSE '11+' END"""

PER_OWNER_DISTRIBUTION = (
    PER_OWNER_DISTRIBUTION
    .replace("bucket_of(n_properties)", _BUCKET_CASE.format(col="n_properties"))
    .replace("bucket_of(n_renters)", _BUCKET_CASE.format(col="n_renters"))
)

# Bucket order for the chart — SQL returns them as text, which sorts '11+' before '2'.
BUCKET_ORDER = ["0", "1", "2", "3-5", "6-10", "11+"]


# ─────────────────────────────────────────────────────────────────────────────
# AI features
# ─────────────────────────────────────────────────────────────────────────────

# `submitted_at IS NULL` means abandoned — but the outcome is recorded by a client-driven
# PATCH, so a save whose callback never arrived also reads as abandoned. Treat the
# abandonment share as an upper bound; it is labelled that way on the page.
LEASE_SCANS_OVER_TIME = f"""
WITH {SCOPED_OWNERS}
SELECT date_trunc(CAST(:granularity AS text), l.created_at)                    AS bucket,
       count(*)                                                  AS scans,
       count(*) FILTER (WHERE l.submitted_at IS NOT NULL)        AS saved,
       count(*) FILTER (WHERE l.status <> 'success')             AS failed,
       round(avg(l.latency_ms)::numeric, 0)                      AS avg_latency_ms,
       round(sum(l.estimated_cost_usd)::numeric, 4)              AS cost_usd
FROM document_extraction_logs l
JOIN scoped_owners s ON s.id = l.owner_id
WHERE l.created_at >= :since AND l.created_at < :until
GROUP BY 1
ORDER BY 1
"""

# The extraction-accuracy headline: of the fields the model pre-filled and the user kept or
# corrected, what share did they have to correct.
EXTRACTION_ACCURACY = f"""
WITH {SCOPED_OWNERS}
SELECT count(*)                                                   AS submitted_scans,
       sum(l.fields_given_count)                                  AS fields_given,
       sum(l.fields_changed_count)                                AS fields_changed,
       round(avg(l.fields_changed_count)::numeric, 2)             AS avg_changed_per_scan,
       round(avg(l.fields_extracted)::numeric, 2)                 AS avg_extracted_per_scan,
       round(avg(l.low_confidence_count)::numeric, 2)             AS avg_low_confidence,
       CASE WHEN sum(l.fields_given_count) > 0
            THEN round(100.0 * sum(l.fields_changed_count) / sum(l.fields_given_count), 1)
            END                                                   AS pct_fields_corrected
FROM document_extraction_logs l
JOIN scoped_owners s ON s.id = l.owner_id
WHERE l.created_at >= :since AND l.created_at < :until
  AND l.submitted_at IS NOT NULL
"""

# 🔴 PII BOUNDARY. `field_edits` is a JSON array of
#     {section, field, prefilled_value, submitted_value, source_text}
# and the last three are extracted lease content — renter names, phone numbers, addresses.
# This projects `section` and `field` ONLY. Do not add the values to this SELECT, and do not
# "just for debugging" return the raw column from the endpoint.
#
# The column is Text, not jsonb, so it is cast per row and cannot be indexed; the scan is
# bounded by the date filter on the outer table.
CORRECTED_FIELDS_BY_NAME = f"""
WITH {SCOPED_OWNERS}
SELECT e.value ->> 'section'          AS section,
       e.value ->> 'field'            AS field,
       count(*)                       AS corrections,
       count(DISTINCT l.id)           AS scans_affected
FROM document_extraction_logs l
JOIN scoped_owners s ON s.id = l.owner_id
CROSS JOIN LATERAL jsonb_array_elements(l.field_edits::jsonb) AS e(value)
WHERE l.created_at >= :since AND l.created_at < :until
  AND l.field_edits IS NOT NULL
  AND l.field_edits <> ''
GROUP BY 1, 2
ORDER BY corrections DESC
LIMIT 40
"""

ASSISTANT_USAGE_OVER_TIME = f"""
WITH {SCOPED_OWNERS}
SELECT date_trunc(CAST(:granularity AS text), u.created_at)                  AS bucket,
       count(*)                                                AS messages,
       count(DISTINCT u.owner_id)                              AS owners,
       count(*) FILTER (WHERE u.status <> 'success')           AS failures,
       round(sum(u.estimated_cost_usd)::numeric, 4)            AS cost_usd,
       count(*) FILTER (WHERE u.estimated_cost_usd IS NULL)    AS rows_missing_cost
FROM agent_usage_logs u
JOIN scoped_owners s ON s.id = u.owner_id
WHERE u.created_at >= :since AND u.created_at < :until
GROUP BY 1
ORDER BY 1
"""

# PROXY, not a measurement. A request rejected by the daily cap deletes its own reservation
# row (`agent_service._enforce_limits` -> `repo.delete_usage_log`), so a 429 leaves no trace.
# What this counts is owners who *reached* the cap. Whether they wanted a 51st message is not
# knowable from the database. Labelled as an estimate on the page.
ASSISTANT_CAP_HITS = f"""
WITH {SCOPED_OWNERS},
per_owner_day AS (
    SELECT u.owner_id, date_trunc('day', u.created_at) AS day, count(*) AS messages
    FROM agent_usage_logs u
    JOIN scoped_owners s ON s.id = u.owner_id
    WHERE u.created_at >= :since AND u.created_at < :until
    GROUP BY 1, 2
)
SELECT count(*) FILTER (WHERE messages >= :daily_message_limit)          AS owner_days_at_cap,
       count(DISTINCT owner_id) FILTER (WHERE messages >= :daily_message_limit) AS owners_at_cap,
       max(messages)                                                     AS busiest_owner_day
FROM per_owner_day
"""

# Same proxy caveat, plus one more: the threshold is an environment variable with no history
# in the database, so this compares every past day against *today's* limit.
ASSISTANT_GLOBAL_SPEND = """
SELECT date_trunc('day', created_at)                  AS day,
       round(sum(estimated_cost_usd)::numeric, 4)     AS cost_usd,
       count(*)                                       AS messages
FROM agent_usage_logs
WHERE created_at >= :since AND created_at < :until
GROUP BY 1
ORDER BY 1
"""


# ─────────────────────────────────────────────────────────────────────────────
# Platform
# ─────────────────────────────────────────────────────────────────────────────

# Devices and owners are both reported: one landlord with a phone and a laptop is two device
# rows but one owner, and the owner count is the honest answer to "what do my users use".
DEVICE_PLATFORM_SPLIT = f"""
WITH {SCOPED_OWNERS}
SELECT d.platform::text            AS platform,
       count(*)                    AS devices,
       count(DISTINCT d.owner_id)  AS owners
FROM device_tokens d
JOIN scoped_owners s ON s.id = d.owner_id
GROUP BY 1
ORDER BY 2 DESC
"""

# Where owners actually WORK, from the client-usage counters. `writes` is the load-bearing
# column: rows and `requests` count a five-second app open the same as an evening of work,
# which is exactly the question this table exists to answer.
CLIENT_USAGE_SPLIT = f"""
WITH {SCOPED_OWNERS}
SELECT c.app,
       c.platform,
       count(DISTINCT c.owner_id)  AS owners,
       sum(c.requests)             AS requests,
       sum(c.writes)               AS writes,
       count(*)                    AS owner_days
FROM owner_client_days c
JOIN scoped_owners s ON s.id = c.owner_id
WHERE c.day >= :since AND c.day < :until
GROUP BY 1, 2
ORDER BY writes DESC NULLS LAST
"""

CLIENT_USAGE_OVER_TIME = f"""
WITH {SCOPED_OWNERS}
SELECT date_trunc(CAST(:granularity AS text), c.day) AS bucket,
       c.app,
       count(DISTINCT c.owner_id)      AS owners,
       sum(c.writes)                   AS writes
FROM owner_client_days c
JOIN scoped_owners s ON s.id = c.owner_id
WHERE c.day >= :since AND c.day < :until
GROUP BY 1, 2
ORDER BY 1, 2
"""

# mobile-only / web-only / both, sized by work done rather than headcount.
CLIENT_OVERLAP = f"""
WITH {SCOPED_OWNERS},
per_owner AS (
    SELECT c.owner_id,
           bool_or(c.app = 'mobile') AS on_mobile,
           bool_or(c.app = 'web')    AS on_web,
           sum(c.writes)             AS writes
    FROM owner_client_days c
    JOIN scoped_owners s ON s.id = c.owner_id
    WHERE c.day >= :since AND c.day < :until
    GROUP BY c.owner_id
)
SELECT CASE WHEN on_mobile AND on_web THEN 'both'
            WHEN on_mobile            THEN 'mobile only'
            WHEN on_web               THEN 'web only'
            ELSE 'unknown' END  AS segment,
       count(*)                 AS owners,
       sum(writes)              AS writes
FROM per_owner
GROUP BY 1
ORDER BY owners DESC
"""

# Signup platform and language both come from legal_acceptances: the consent gate is
# mandatory, so every owner past it has a row. DISTINCT owner is essential — the table is
# append-only and keeps repeat acceptances, including the same person on two devices.
SIGNUP_PLATFORM_SPLIT = f"""
WITH {SCOPED_OWNERS},
first_acceptance AS (
    SELECT DISTINCT ON (la.owner_id) la.owner_id, la.platform::text AS platform, la.locale
    FROM legal_acceptances la
    JOIN scoped_owners s ON s.id = la.owner_id
    ORDER BY la.owner_id, la.accepted_at
)
SELECT platform, count(*) AS owners
FROM first_acceptance
GROUP BY 1
ORDER BY 2 DESC
"""

# Preferred over device_tokens.locale: that only exists for owners who enabled push, so it
# samples a biased subset. The consent gate covers everyone.
LANGUAGE_SPLIT = f"""
WITH {SCOPED_OWNERS},
first_acceptance AS (
    SELECT DISTINCT ON (la.owner_id) la.owner_id, la.locale
    FROM legal_acceptances la
    JOIN scoped_owners s ON s.id = la.owner_id
    ORDER BY la.owner_id, la.accepted_at
)
SELECT locale, count(*) AS owners
FROM first_acceptance
GROUP BY 1
ORDER BY 2 DESC
"""

# Both countries, because they answer different questions: where the account holder signed up
# (who your users are) and where the buildings are (whose rules and features you must build).
COUNTRY_SPLIT = f"""
WITH {SCOPED_OWNERS},
prop_counts AS (SELECT owner_id, count(*) AS n FROM properties GROUP BY owner_id)
SELECT s.country                      AS country,
       count(*)                       AS owners,
       COALESCE(sum(p.n), 0)          AS properties
FROM scoped_owners s
LEFT JOIN prop_counts p ON p.owner_id = s.id
GROUP BY s.country
ORDER BY owners DESC
"""

PROPERTY_COUNTRY_SPLIT = f"""
WITH {SCOPED_OWNERS}
SELECT COALESCE(p.country, 'IL (assumed)') AS country,
       count(*)                            AS properties,
       count(DISTINCT p.owner_id)          AS owners
FROM properties p
JOIN scoped_owners s ON s.id = p.owner_id
GROUP BY 1
ORDER BY 2 DESC
"""


# ─────────────────────────────────────────────────────────────────────────────
# Operations — job health and data health
# ─────────────────────────────────────────────────────────────────────────────

# A silently dead cron is invisible: users simply stop getting rent reminders. job_runs is
# the durable record of what actually ran, and costs nothing to read.
JOB_HEALTH = """
SELECT job_name,
       count(*)                                                    AS runs,
       max(started_at)                                             AS last_run,
       max(started_at) FILTER (WHERE status = 'ok')                AS last_ok,
       count(*) FILTER (WHERE status = 'ok')                       AS ok_runs,
       count(*) FILTER (WHERE status IN ('failed','stale','degraded')) AS bad_runs,
       count(*) FILTER (WHERE finished_at IS NULL)                 AS unfinished
FROM job_runs
WHERE started_at >= :since
GROUP BY job_name
ORDER BY job_name
"""

# Replaces the one-off probe script: the dashboard reports its own data health, so the
# horizons and volumes are visible without exposing Postgres to the public internet.
DATA_HEALTH = """
SELECT 'owners' AS table_name, count(*) AS rows, min(created_at) AS earliest, max(created_at) AS latest FROM owners
UNION ALL SELECT 'properties',              count(*), min(created_at),  max(created_at)  FROM properties
UNION ALL SELECT 'renters',                 count(*), min(created_at),  max(created_at)  FROM renters
UNION ALL SELECT 'transactions',            count(*), min(created_at),  max(created_at)  FROM transactions
UNION ALL SELECT 'report_exports',          count(*), min(created_at),  max(created_at)  FROM report_exports
UNION ALL SELECT 'document_extraction_logs',count(*), min(created_at),  max(created_at)  FROM document_extraction_logs
UNION ALL SELECT 'agent_usage_logs',        count(*), min(created_at),  max(created_at)  FROM agent_usage_logs
UNION ALL SELECT 'device_tokens',           count(*), min(created_at),  max(created_at)  FROM device_tokens
UNION ALL SELECT 'legal_acceptances',       count(*), min(accepted_at), max(accepted_at) FROM legal_acceptances
UNION ALL SELECT 'activity_log',            count(*), min(created_at),  max(created_at)  FROM activity_log
UNION ALL SELECT 'owner_client_days',       count(*), min(first_seen_at), max(first_seen_at) FROM owner_client_days
UNION ALL SELECT 'deleted_accounts',        count(*), min(deleted_at),  max(deleted_at)  FROM deleted_accounts
ORDER BY 1
"""

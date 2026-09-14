"""One-off, read-only probe of the production database for the admin analytics dashboard.

Run it, paste the output back. It is Step 0 of ANALYTICS_FEASIBILITY.md: confirm the real
schema, find out how much data exists, check which indexes actually exist, and locate the
admin's owner id for ADMIN_OWNER_IDS.

    cd rent-control-backend
    railway link                       # once: pick the project, then rent-control-backend
    railway run python ops/analytics_probe.py

Safety, so you can check rather than trust:

* The session is put into ``default_transaction_read_only = on`` before anything else runs.
  Postgres itself then rejects any INSERT/UPDATE/DELETE/DDL from this connection, so a
  mistake in this file cannot write to your database.
* Every statement below is a SELECT. There is no write path, and no DDL.
* **It prints no tenant data.** Counts, date ranges, type names and distributions only.
  The one identifying value it prints is *your own* owner id, looked up by *your own*
  email, because ADMIN_OWNER_IDS needs it. No renter names, addresses, phones or emails
  are selected anywhere — grep for 'first_name' and you will not find it.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import psycopg2

# Tables the dashboard reads, with the timestamp column each metric keys on. Kept in one
# place so the horizon report below and the SQL file stay in step.
ANALYTICS_TABLES: list[tuple[str, str | None]] = [
    ("owners", "created_at"),
    ("properties", "created_at"),
    ("renters", "created_at"),
    ("transactions", "created_at"),
    ("report_exports", "created_at"),
    ("document_extraction_logs", "created_at"),
    ("agent_conversations", "created_at"),
    ("agent_messages", "created_at"),
    ("agent_usage_logs", "created_at"),
    ("device_tokens", "created_at"),
    ("activity_log", "created_at"),
    ("deleted_accounts", "deleted_at"),
    ("job_runs", "started_at"),
    ("owner_client_days", "first_seen_at"),
    ("suppliers", "created_at"),
    ("expense_categories", "created_at"),
    ("notifications", None),
    ("legal_acceptances", "accepted_at"),
    ("property_files", None),
]

# Columns worth dumping in full; the rest are listed by name only to keep output short.
DETAIL_TABLES = {
    "owners",
    "properties",
    "renters",
    "transactions",
    "report_exports",
    "document_extraction_logs",
    "agent_usage_logs",
    "device_tokens",
    "activity_log",
    "deleted_accounts",
    "job_runs",
}


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def q(cur, sql: str, params: tuple = ()) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--email",
        default="eyalkook@gmail.com",
        help="Your login email, used only to find your owner id for ADMIN_OWNER_IDS.",
    )
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        print(
            "DATABASE_URL is not set.\n"
            "\n"
            "`railway run` injects the variables of the *linked service*, and the linked one\n"
            "does not have DATABASE_URL. Name the backend explicitly:\n"
            "\n"
            "    railway run --service rent-control-backend python ops/analytics_probe.py\n"
            "\n"
            "`railway status` shows what is currently linked.",
            file=sys.stderr,
        )
        return 1

    safe = re.sub(r"://[^@]*@", "://<redacted>@", url)
    print(f"host: {safe}")

    # Railway's internal hostname only resolves inside their network. `railway run` injects
    # variables into a local process but does not tunnel, so this cannot work from a laptop.
    if ".railway.internal" in url:
        print(
            "\n"
            "This is Railway's INTERNAL hostname. It resolves only inside Railway's network,\n"
            "so this script cannot reach it from your machine — `railway run` passes variables\n"
            "through, it does not open a tunnel.\n"
            "\n"
            "To get a probe run, pick one:\n"
            "\n"
            "  a) Temporarily expose Postgres: Railway -> Postgres -> Settings -> Public\n"
            "     Networking -> add a TCP proxy. Re-run with the public URL:\n"
            "         DATABASE_URL='<public url>' python ops/analytics_probe.py\n"
            "     Then REMOVE the proxy again. Exposure lasts only as long as the probe.\n"
            "\n"
            "  b) Skip the probe. The dashboard runs inside the backend and uses this same\n"
            "     internal URL, so it never needs public access — only this one-off does.\n"
            "     Say so and the build proceeds on the schema read from the repo, with the\n"
            "     numbers verified later through the dashboard itself.",
            file=sys.stderr,
        )
        return 2

    conn = psycopg2.connect(url, connect_timeout=10)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    # Belt and braces: ask the server to refuse writes on this session too.
    cur.execute("SET default_transaction_read_only = on")

    print("server:", q(cur, "SELECT version()")[0][0].split(",")[0])
    print("database:", q(cur, "SELECT current_database()")[0][0])
    print("connected as:", q(cur, "SELECT current_user")[0][0])
    print("read-only session:", q(cur, "SHOW default_transaction_read_only")[0][0])

    # ── 1. every table, with row counts ────────────────────────────────────────
    rule("1. TABLES AND ROW COUNTS")
    tables = [r[0] for r in q(
        cur,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name",
    )]
    print(f"{len(tables)} tables in public\n")
    print(f"{'table':<34}{'rows':>12}{'size':>12}")
    print("-" * 58)
    for t in tables:
        n = q(cur, f'SELECT count(*) FROM "{t}"')[0][0]
        size = q(cur, "SELECT pg_size_pretty(pg_total_relation_size(%s))", (t,))[0][0]
        print(f"{t:<34}{n:>12,}{size:>12}")

    # ── 2. alembic position ────────────────────────────────────────────────────
    rule("2. MIGRATION STATE")
    if "alembic_version" in tables:
        print("alembic head in DB:", q(cur, "SELECT version_num FROM alembic_version")[0][0])
        print("(repo has 56 migration files; newest is 20260914_report_export_revenue_basis)")
    else:
        print("no alembic_version table")

    # ── 3. columns ─────────────────────────────────────────────────────────────
    rule("3. COLUMNS")
    for t in tables:
        cols = q(
            cur,
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema='public' AND table_name=%s "
            "ORDER BY ordinal_position",
            (t,),
        )
        if t in DETAIL_TABLES:
            print(f"\n--- {t} ---")
            for name, dtype, nullable, default in cols:
                d = f"  default={default}" if default else ""
                print(f"  {name:<30}{dtype:<28}{'NULL' if nullable == 'YES' else 'NOT NULL':<9}{d}")
        else:
            print(f"\n--- {t} --- ({len(cols)} cols) " + ", ".join(c[0] for c in cols))

    # ── 4. indexes — the ones the dashboard's queries will or won't get to use ──
    rule("4. INDEXES (actual, from pg_indexes)")
    for t in tables:
        idx = q(cur, "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public' AND tablename=%s ORDER BY indexname", (t,))
        if idx:
            print(f"\n--- {t} ---")
            for name, definition in idx:
                print(f"  {name}\n      {definition.split('USING')[-1].strip()}")

    print("\nSpecifically checking the three the feasibility report flagged:")
    for name in (
        "ix_transactions_date_of_payment",
        "ix_transactions_property_type_date",
        "ix_transactions_owner_id",
    ):
        found = q(cur, "SELECT 1 FROM pg_indexes WHERE indexname=%s", (name,))
        print(f"  {name:<40}{'EXISTS' if found else 'MISSING'}")

    # ── 5. data horizons — confirm the backfill cliff for real ─────────────────
    rule("5. DATA HORIZONS (earliest/latest row per table)")
    print(f"{'table':<30}{'rows':>8}  {'earliest':<21}{'latest':<21}")
    print("-" * 82)
    for t, ts in ANALYTICS_TABLES:
        if t not in tables or ts is None:
            continue
        row = q(cur, f'SELECT count(*), min("{ts}"), max("{ts}") FROM "{t}"')[0]
        n, lo, hi = row
        print(f"{t:<30}{n:>8,}  {str(lo)[:19]:<21}{str(hi)[:19]:<21}")

    print("\nHow many rows sit exactly on the 2026-07-02 backfill timestamp:")
    for t in ("properties", "renters"):
        if t not in tables:
            continue
        total = q(cur, f'SELECT count(*) FROM "{t}"')[0][0]
        pre = q(cur, f"SELECT count(*) FROM \"{t}\" WHERE created_at < '2026-07-03'")[0][0]
        print(f"  {t:<14}{pre:>6} of {total} created before 2026-07-03 (i.e. probably backfilled)")

    # ── 6. country — the new dimension ─────────────────────────────────────────
    rule("6. COUNTRY")
    print("owners.country (signup country) — 'created before 2026-09-13' means backfilled to IL:")
    for country, backfilled, n in q(
        cur,
        "SELECT coalesce(country,'(null)'), created_at < '2026-09-13', count(*) "
        "FROM owners GROUP BY 1, 2 ORDER BY 3 DESC",
    ):
        tag = "backfilled" if backfilled else "chosen"
        print(f"  {country:<8}{tag:<14}{n:>6}")

    print("\nproperties.country (where the building is):")
    for country, n in q(
        cur,
        "SELECT coalesce(country,'(null → treated as IL)'), count(*) "
        "FROM properties GROUP BY 1 ORDER BY 2 DESC",
    ):
        print(f"  {country:<26}{n:>6}")

    # ── 7. shape of the enums and distributions the dashboard charts ───────────
    rule("7. DISTRIBUTIONS")
    print("device_tokens by platform:")
    for row in q(cur, "SELECT platform::text, count(*), count(DISTINCT owner_id) FROM device_tokens GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {row[0]:<14}{row[1]:>6} devices  {row[2]:>6} owners")

    print("\ndevice_tokens by locale:")
    for row in q(cur, "SELECT coalesce(locale,'(null)'), count(*) FROM device_tokens GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {row[0]:<14}{row[1]:>6}")

    # owner_client_days — where owners actually work. `writes` is the column that matters:
    # a five-second app open and an evening of real work both produce one row, and only the
    # counters tell them apart.
    if "owner_client_days" in tables:
        print("client usage — rows, and how much work happened in each client:")
        for row in q(
            cur,
            "SELECT app, platform, count(*), count(DISTINCT owner_id), "
            "       sum(requests), sum(writes) "
            "FROM owner_client_days GROUP BY 1, 2 ORDER BY 6 DESC NULLS LAST",
        ):
            print(
                f"  {row[0]:<9}{row[1]:<9}{row[2]:>5} rows  {row[3]:>4} owners  "
                f"{row[4] or 0:>7} requests  {row[5] or 0:>6} writes"
            )

        print("\n  mobile-only / web-only / both, by owner (all time):")
        for row in q(
            cur,
            "SELECT segment, count(*) FROM ("
            "  SELECT owner_id, CASE"
            "    WHEN bool_or(app='mobile') AND bool_or(app='web') THEN 'both'"
            "    WHEN bool_or(app='mobile') THEN 'mobile only'"
            "    WHEN bool_or(app='web') THEN 'web only'"
            "    ELSE 'unknown' END AS segment"
            "  FROM owner_client_days GROUP BY owner_id) s GROUP BY 1 ORDER BY 2 DESC",
        ):
            print(f"    {row[0]:<14}{row[1]:>6} owners")

        print("\n  app versions seen:")
        for row in q(
            cur,
            "SELECT app, coalesce(app_version,'(none)'), count(DISTINCT owner_id) "
            "FROM owner_client_days GROUP BY 1,2 ORDER BY 3 DESC",
        ):
            print(f"    {row[0]:<9}{row[1]:<14}{row[2]:>5} owners")

        print("\n  sanity: rows where writes > requests (should be 0 — the allowlist is a subset):")
        print(f"    {q(cur, 'SELECT count(*) FROM owner_client_days WHERE writes > requests')[0][0]}")

    # legal_acceptances is the only place signup platform and an unbiased language choice
    # are recorded — device_tokens.locale only covers owners who enabled push.
    print("\nlegal_acceptances — signup platform (distinct owners, not rows):")
    for row in q(
        cur,
        "SELECT platform::text, count(DISTINCT owner_id) FROM legal_acceptances GROUP BY 1 ORDER BY 2 DESC",
    ):
        print(f"  {row[0]:<14}{row[1]:>6} owners")

    print("\nlegal_acceptances — language the documents were read in:")
    for row in q(
        cur,
        "SELECT locale, count(DISTINCT owner_id) FROM legal_acceptances GROUP BY 1 ORDER BY 2 DESC",
    ):
        print(f"  {row[0]:<14}{row[1]:>6} owners")

    print("\n  owners with a legal_acceptances row vs owners total")
    print("  (a large gap means signup platform is unavailable for most owners):")
    row = q(
        cur,
        "SELECT (SELECT count(*) FROM owners), "
        "       (SELECT count(DISTINCT owner_id) FROM legal_acceptances)",
    )[0]
    print(f"    {row[1]} of {row[0]} owners have accepted terms")

    print("\n  owners appearing on more than one platform (cross-client users):")
    row = q(
        cur,
        "SELECT count(*) FROM (SELECT owner_id FROM legal_acceptances "
        "GROUP BY owner_id HAVING count(DISTINCT platform) > 1) s",
    )[0]
    print(f"    {row[0]}")

    print("\ndocument_extraction_logs by status, and how many were submitted:")
    for row in q(
        cur,
        "SELECT status, count(*), count(*) FILTER (WHERE submitted_at IS NOT NULL) FROM document_extraction_logs GROUP BY 1 ORDER BY 2 DESC",
    ):
        print(f"  {row[0]:<14}{row[1]:>6} scans, {row[2]} submitted")

    print("\nagent_usage_logs by status:")
    for row in q(cur, "SELECT status, count(*), round(sum(estimated_cost_usd)::numeric, 4), count(*) FILTER (WHERE estimated_cost_usd IS NULL) FROM agent_usage_logs GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {row[0]:<14}{row[1]:>6} msgs, ${row[2]} total, {row[3]} rows with NULL cost")

    print("\nagent_usage_logs by model (NULL cost ⇒ model missing from the price table):")
    for row in q(cur, "SELECT coalesce(model,'(null)'), count(*), count(*) FILTER (WHERE estimated_cost_usd IS NULL) FROM agent_usage_logs GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {row[0]:<26}{row[1]:>6} msgs, {row[2]} with NULL cost")

    print("\nactivity_log by action/entity (confirms what is being logged today):")
    for row in q(cur, "SELECT action, entity_type, count(*) FROM activity_log GROUP BY 1,2 ORDER BY 3 DESC"):
        print(f"  {row[0]:<12}{row[1]:<14}{row[2]:>6}")

    print("\nreport_exports by type/format:")
    for row in q(cur, "SELECT report_type::text, format::text, count(*) FROM report_exports GROUP BY 1,2 ORDER BY 3 DESC"):
        print(f"  {row[0]:<18}{row[1]:<8}{row[2]:>6}")

    print("\nproperties per owner (the histogram's raw shape):")
    for row in q(
        cur,
        "SELECT n, count(*) FROM (SELECT owner_id, count(*) AS n FROM properties GROUP BY 1) s GROUP BY 1 ORDER BY 1",
    ):
        print(f"  {row[0]:>3} properties: {row[1]:>5} owners")

    # ── 8. job health ──────────────────────────────────────────────────────────
    rule("8. BACKGROUND JOB HEALTH (job_runs)")
    # status is 'ok | degraded | stale | failed'; finished_at stays NULL if the process died.
    for row in q(
        cur,
        "SELECT job_name, count(*), max(started_at), "
        "       count(*) FILTER (WHERE status = 'ok'), "
        "       count(*) FILTER (WHERE status IN ('failed', 'stale', 'degraded')), "
        "       count(*) FILTER (WHERE finished_at IS NULL) "
        "FROM job_runs GROUP BY 1 ORDER BY 1",
    ):
        print(
            f"  {row[0]:<22}{row[1]:>5} runs, last {str(row[2])[:19]}, "
            f"{row[3]} ok / {row[4]} bad / {row[5]} never finished"
        )

    print("\n  status values actually present:")
    for row in q(cur, "SELECT status, count(*) FROM job_runs GROUP BY 1 ORDER BY 2 DESC"):
        print(f"    {row[0]:<12}{row[1]:>6}")

    # ── 9. the admin's own owner id, for ADMIN_OWNER_IDS ───────────────────────
    rule("9. ADMIN_OWNER_IDS")
    hit = q(cur, "SELECT id, created_at, country FROM owners WHERE lower(email) = lower(%s)", (args.email,))
    if hit:
        print(f"  ADMIN_OWNER_IDS={hit[0][0]}")
        print(f"  (owner created {str(hit[0][1])[:19]}, country {hit[0][2]})")
    else:
        print(f"  No owner row for {args.email}.")
        print("  Owner ids and their signup dates, emails masked so nothing identifying is printed:")
        for oid, created, masked in q(
            cur,
            "SELECT id, created_at, "
            "  coalesce(left(email,2) || '***@' || split_part(email,'@',2), '(no email)') "
            "FROM owners ORDER BY created_at LIMIT 25",
        ):
            print(f"    {oid}  {str(created)[:19]}  {masked}")

    # ── 10. plans for the two heaviest query shapes ────────────────────────────
    rule("10. EXPLAIN (indicative only — plans on a small table say little about scale)")
    plans = {
        "first transaction per owner (funnel step 4)":
            "SELECT owner_id, min(created_at) FROM transactions GROUP BY owner_id",
        "weekly active owners (union of write events)":
            "SELECT date_trunc('week', t) AS wk, count(DISTINCT owner_id) FROM ("
            "  SELECT owner_id, created_at AS t FROM properties"
            "  UNION ALL SELECT owner_id, created_at FROM renters"
            "  UNION ALL SELECT owner_id, created_at FROM transactions"
            "  UNION ALL SELECT owner_id, created_at FROM report_exports"
            "  UNION ALL SELECT owner_id, created_at FROM document_extraction_logs"
            "  UNION ALL SELECT owner_id, created_at FROM agent_usage_logs"
            "  UNION ALL SELECT owner_id, created_at FROM activity_log"
            ") e GROUP BY 1",
        "corrected fields by field name (the jsonb scan)":
            "SELECT e->>'field' AS f, count(*) FROM document_extraction_logs l,"
            " LATERAL jsonb_array_elements(l.field_edits::jsonb) AS e"
            " WHERE l.field_edits IS NOT NULL GROUP BY 1",
    }
    for label, sql in plans.items():
        print(f"\n--- {label} ---")
        try:
            for line in q(cur, "EXPLAIN (ANALYZE, BUFFERS, TIMING) " + sql):
                print("   ", line[0])
        except Exception as exc:  # noqa: BLE001 - probe should keep going
            print("    failed:", str(exc).strip().splitlines()[0])

    cur.close()
    conn.close()
    print("\ndone — nothing was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

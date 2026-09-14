# Admin analytics dashboard — Step 0 feasibility report

**Status: Step 0 findings below; decisions taken 2026-09-14, recorded here.**
Date: 2026-09-14. Source: SQLAlchemy models + all 56 Alembic migrations.

## Decisions taken

| # | Question | Decision |
|---|---|---|
| 1 | Database access | **Owner runs `ops/analytics_probe.py` via `railway run`.** No public proxy, no connection held by the assistant. |
| 2 | `analytics_ro` role | **Skipped** — not needed when the probe is run by the owner, and the dashboard uses the backend's existing connection. |
| 3 | 2026-07-02 data cliff | **Accept it.** App is pre-launch, so affected rows are test data. Cohort/funnel metrics start 2026-07-06. |
| 3b | Synthetic data to verify the SQL | **No.** Accepted consequence: funnel monotonicity, cohort week alignment and histogram bucket edges stay unverified until real data exists. Mitigated by a self-check in the endpoint (below). |
| 4 | Signup method (email vs Google) | **Dropped.** Firebase retains it permanently, so deferring loses nothing and it can be backfilled whenever it matters. |
| 5 | Edit logging | **Wanted, handed off.** See `ACTIVITY_LOG_EDIT_LOGGING_PROMPT.md` — a separate session. Until it lands, WAU misses edit-only weeks. |
| 6 | Assistant cap hits | **Labelled proxy** (owners reaching the daily message limit). No rejection logging added. |
| 7 | Admin owner id | Probe script resolves it from the owner's email. |
| 8 | External sources | **In scope:** Firebase Auth (true signup count), Firebase Storage (bytes), Railway (cost), plus `job_runs` health from Postgres. Each on its own endpoint and cache so one outage cannot take down the page. |
| 9 | Country | **A first-class global filter**, not a single chart. Every query accepts it. `owners.country` drives the user filter; `properties.country` shown alongside. Owners created before 2026-09-13 are reported as *unknown*, not Israel — their value was backfilled. |
| 10 | Chart library | Chart.js. |
| 11 | Client split (mobile vs web) | **Signup platform is available now** via `legal_acceptances.platform` — the consent gate is mandatory, so every owner since 2026-09-08 has one. **Where an owner *works* is not recorded at all**; neither API client sends a platform header. Handed off: `../CLIENT_PLATFORM_TRACKING_PROMPT.md` (spans all three repos). |
| 12 | Language source | Switched to **`legal_acceptances.locale`**, not `device_tokens.locale`. Both record `en`/`he`, but the consent gate covers every owner while a device token only exists for those who enabled push — so the original choice sampled only notification-enabled users. |

**Self-check replacing synthetic-data verification:** the analytics endpoint asserts funnel
monotonicity (each step ≤ the one before) and returns a warning in its own payload if the
invariant breaks, so a wrong join surfaces the day real data arrives rather than being
trusted silently.

> **Caveat on this report:** I could not connect to the production database (see §0).
> Everything below is derived from the schema source of truth in the repo, so
> column/index/shape claims are reliable. **Row counts, data volumes, and whether any
> metric has enough data to be worth charting are unverified.**

---

## 0. Blocker: no route to the production database

The brief assumes `DATABASE_PUBLIC_URL` on the Railway Postgres service. It does not exist:

| Check | Result |
|---|---|
| Postgres service vars | `DATABASE_URL`, `PGHOST`, `PGPORT`… — **no `DATABASE_PUBLIC_URL`** |
| TCP proxies on Postgres | **none** — the DB has no public endpoint at all |
| Railway MCP variable values | redacted (OAuth apps get names only), so I cannot read `DATABASE_URL` |
| Local `railway` CLI | not installed |
| Local `.env` | points at `localhost:5432` — a dev DB, not production |
| `psql` | not installed (`psycopg2` 2.9.11 is available) |

So there is currently **no way for me to reach the production database**, with the
`analytics_ro` role or any other. Step 0's schema dump, the row counts, and every
`EXPLAIN` in §7 are blocked on this.

Separately: a sandbox policy in this session blocks me from opening database connections
at all, including to the local dev DB.

**Your options** — this is the first decision I need from you:

1. **Add a TCP proxy** to the Postgres service (Railway → Postgres → Settings → Public
   Networking). That produces the public host:port the brief assumed. Then create
   `analytics_ro` and give me that connection string.
2. **Run the queries yourself.** I write the SQL file (deliverable §8.2), you run it and
   paste results back. Slowest loop, zero new exposure.
3. **Install the Railway CLI** and run `railway run python <script>` locally, which
   injects the real `DATABASE_URL` without exposing the DB publicly. Good middle ground.

I'd suggest **3** for Step 0 (schema + counts + EXPLAIN are all one-off) and no public
proxy at all — the dashboard itself runs inside the backend service on the existing
internal `DATABASE_URL`, so it never needs public access.

**On creating the `analytics_ro` role:** that is DDL, i.e. a write, which brief §2 says I
must ask about first. It is also *not needed for the dashboard* — the endpoint runs in the
backend process on the app's existing connection. It is only worth creating if you want me
(or a future MCP server) querying the DB directly. Your call; I have not run it.

---

## 1. Where the data actually starts

**This is the most important finding in this report and it constrains the whole
dashboard.** Tables and timestamp columns were added at different times, and two were
backfilled with the migration date.

| Table / column | Real data from | Note |
|---|---|---|
| `transactions.created_at` | **2025-03-14** | Present since the table was created. Trustworthy. |
| `report_exports` | 2026-05-14 | Table created. |
| `device_tokens` (+ `locale`) | 2026-06-11 | Table created. |
| `document_extraction_logs` | 2026-06-25 | Table created. |
| `owners` | **2026-07-01** | ⚠️ see (a) below |
| `properties.created_at`, `renters.created_at` | **2026-07-02** | ⚠️ **backfilled to `now()`** |
| `agent_usage_logs` | 2026-07-26 | Table created. |
| `deleted_accounts`, `activity_log` | 2026-07-31 | Tables created. |

Two problems follow:

**(a) `owners.created_at` is not the signup date for anyone who signed up before
2026-07-01.** The table was created empty; a row is written on the owner's *next*
authenticated request (`OwnerRepository.upsert`). So a landlord who signed up in January
2026 has `created_at` = whenever they next opened the app after that deploy. Their true
signup date lives in Firebase Auth and is not in Postgres.

**(b) `properties.created_at` and `renters.created_at` were backfilled to the migration
timestamp** for every pre-existing row — migration `031` says so explicitly: *"Existing
rows are backfilled with the migration time (now()) since their true creation date is
unknown."*

Combined effect: **everything before ~2026-07-02 collapses into a spike on that date**,
and *"median time from signup to first property"* is meaningless for those owners — an
owner whose signup reads 2026-07-01 and whose 18-month-old property reads 2026-07-02 will
show **"1 day to first property."** That is not a small skew; it drags the median toward
zero and makes activation look far better than it is.

Today is 2026-09-14, so there are **~10 weeks of trustworthy signup/activation data.**

**My recommendation:** the dashboard defaults to a window starting **2026-07-06** (the
first full week after the backfill) for every cohort, funnel and time-to-first metric, and
renders a visible "data starts here" marker rather than silently hiding it. "All time"
stays available but carries a warning banner. Transactions and revenue-shaped series can
safely go back to 2025-03-14. **Tell me if you'd rather I picked a different cutoff.**

---

## 2. Metric-by-metric feasibility

Legend: ✅ computable · ⚠️ computable with a caveat you should read · ❌ blocked

### Growth

| Metric | | Notes |
|---|---|---|
| Signups per week | ⚠️ | `owners.created_at`. Valid from 2026-07-01 only (§1a). Also **survivor-biased**: account deletion hard-deletes the `owners` row, so this is "signups that still exist", not "signups". |
| Cumulative owner count | ⚠️ | Same. It is a *live account count*, not all-time signups. I can add back `deleted_accounts` for a true present-day total, but that table stores only `deleted_at` — **no signup date** — so the historical curve cannot be corrected, only today's number. |
| Signup method split (email vs Google) | ❌ | **Blocked. Nothing stores it.** Firebase's `sign_in_provider` claim is on every verified token but is never persisted — `grep` for `provider` across `app/` returns nothing. Needs a new nullable `owners.sign_in_provider` column populated in `OwnerRepository.upsert`. That is a migration + a write, so it needs your go-ahead. Backfilling existing owners is possible via the Firebase Admin SDK (`list_users`) but that is a separate one-off script, not part of this dashboard. **I have not invented a proxy** — there isn't an honest one. |
| Account deletions per month | ✅ | `deleted_accounts.deleted_at`, indexed. Clean, and the table is deliberately PII-free. Starts 2026-07-31. |

### Activation funnel

| Metric | | Notes |
|---|---|---|
| Signed up → property → renter → transaction → report export | ⚠️ | All five timestamps exist and are owner-scoped. Two caveats. **(i)** Distorted before 2026-07-02 (§1b) — this is the metric the backfill hurts most. **(ii)** Renters and transactions carry their own `owner_id` and can exist with no property, so "ever added a renter" is **not** naturally a subset of "ever added a property". Brief §7 requires each step ≤ the previous, so I will define each step *cumulatively* (step 3 = has property **and** has renter), which guarantees monotonicity. The raw "ever did X" numbers will differ; I'll show both so the gap is visible rather than hidden. |
| Median signup → first property / first renter | ⚠️ | `percentile_cont(0.5)`. Computable, but **actively misleading for pre-July owners** (§1b). I will restrict it to owners created on/after the cutoff and show the sample size, rather than report a number I know is wrong. |

**Survivor bias applies to the whole funnel.** `user_service.delete_account` erases
properties, renters, transactions, report exports, extraction logs and agent logs. Anyone
who churned is invisible — so the funnel measures *surviving* users, who are by definition
the ones who activated. Expect it to flatter the product. Worth stating on the page itself.

### Engagement

| Metric | | Notes |
|---|---|---|
| Weekly active owners | ⚠️ | **`owners.last_seen_at` cannot do this** — it is a single scalar holding only the most recent value, so there is no history to build a weekly series from, and it is updated on *any* authenticated request (throttled to 1h), i.e. it measures "opened the app", not a write. **My proposed definition, which I need you to confirm:** an owner is *active in week W* if they have ≥1 append-only write event in W, from a `UNION ALL` of `created_at` across `properties`, `renters`, `transactions`, `report_exports`, `document_extraction_logs`, `agent_usage_logs` and `activity_log` (deletions). **Known gap:** pure *edits* are missed, because `updated_at` is overwritten and keeps only the latest one. Deletions *are* counted (`activity_log` is append-only). So this under-counts an owner whose week was all edits. |
| Retention by cohort, week 1 & 4 | ⚠️ | Same definition, same gap, plus survivor bias and the 2026-07-06 cohort floor. With ~10 trustworthy weeks, **week-4 retention exists for roughly the first 6 cohorts only** — thin. I'll show cohort sizes next to the percentages so a 2-of-3 cohort doesn't read as "67% retention". |
| Properties per owner / renters per owner histogram | ✅ | `GROUP BY owner_id` then bucket. Aggregate-only, no PII. |

### AI features

| Metric | | Notes |
|---|---|---|
| Lease scans per week | ✅ | `document_extraction_logs.created_at`. From 2026-06-25. |
| Saved vs abandoned | ⚠️ | `submitted_at IS NULL` = abandoned. Reliable *in shape*, but the outcome is recorded by a **client-driven** `PATCH /extract/logs/{id}` — if the client crashes, loses network, or the user closes the app after saving, a genuine save is recorded as abandoned. So **abandonment is over-counted by an unknown margin**. Treat it as an upper bound. |
| Avg corrected fields per scan | ✅ | `fields_changed_count` / `fields_given_count`, both server-maintained in `apply_submit_update`. This is the accuracy metric you care most about and it is clean. |
| **Breakdown by field** | ⚠️ | Possible: `field_edits` is a JSON array of `{section, field, prefilled_value, submitted_value, source_text}`. Needs `field_edits::jsonb` + `jsonb_array_elements` — the column is `Text`, not `jsonb`, so no index and a full scan. Probably fine at current volume; I'll `EXPLAIN` it once I have DB access. <br>🔴 **PII TRAP — the biggest one in this brief.** `prefilled_value`, `submitted_value` and `source_text` hold *extracted lease content*: renter names, phone numbers, addresses. The per-field query **must project only `section` and `field`**, never the values. I'll write it that way and flag it in the SQL file. |
| Assistant messages per day | ✅ | `agent_usage_logs`, one row per user message, `created_at` indexed. Survives the 90-day retention sweep (conversations are deleted, usage logs only detached). From 2026-07-26. |
| Owners hitting the 50/day cap | ⚠️ | **Proxy only — no exact answer exists.** `_enforce_limits` calls `repo.delete_usage_log(reserve)` on rejection, so **a 429 leaves no trace in the database**. Best available: count owners with ≥ `AGENT_DAILY_MESSAGE_LIMIT` rows in a UTC day — that is "reached the cap", not "was blocked by it"; whether they tried a 51st is unknowable. I'll label it as such on the page. Making it exact needs a persisted rejection row (a write — your call, and worth it if this number will ever drive a decision). |
| Days the global spend cap was hit | ⚠️ | Same proxy problem: `SUM(estimated_cost_usd)` per UTC day vs `AGENT_GLOBAL_DAILY_COST_LIMIT_USD` (default 20.0). Extra caveat: **the threshold is an env var, not versioned in the DB**, so comparing history assumes it never changed. If you have ever changed it, the historical series is wrong and I should hard-code the change dates. |

### Platform

| Metric | | Notes |
|---|---|---|
| Device token split iOS/Android/web | ⚠️ | `device_tokens.platform` is a clean enum. But it counts **devices, not owners** — one landlord with a phone and a laptop counts twice. I'll show both (device count and distinct-owner count per platform); they answer different questions and the owner count is the honest one for "what do my users use". |
| Language split | ✅ | **Use `legal_acceptances.locale`** (`en`/`he`), from 2026-09-08. The consent gate is mandatory, so this covers every owner — unlike `device_tokens.locale`, which only exists for owners who enabled push and therefore samples a biased subset. Prefer the acceptance row; fall back to the device token only for owners predating the consent gate. |
| Signup platform (mobile vs web) | ✅ | `legal_acceptances.platform`, from 2026-09-08, one per owner via the mandatory gate. Count **distinct owners, not rows** — the table is append-only and keeps repeat acceptances, including the same person on two devices. |
| Where an owner actually works | ❌ | **Not recorded anywhere.** Both API clients send only `Content-Type` and `Authorization`, so the server cannot distinguish mobile from web on any request. `device_tokens.platform` is not a substitute — it means "registered for push here". See `../CLIENT_PLATFORM_TRACKING_PROMPT.md`. |

---

## 3. Index reality check

Aggregate scans over small tables are fine, but two gaps are worth knowing before I write
queries (**from migrations — to be confirmed against the live DB**):

- **`properties` has no index on `owner_id`** at all. Same for `renters`.
- **`transactions` has no index on `owner_id` or `created_at`.** It has `property_id`,
  `renter_id`, `type`, and a composite `(property_id, type, date_of_payment)`. The funnel's
  "first transaction per owner" and the WAU union both want `(owner_id, created_at)`.
- `ix_transactions_date_of_payment` and `ix_transactions_property_type_date` are declared in
  the model's `__table_args__` but **I could not find them in any migration** — they may not
  exist in production. Worth checking in the schema dump.

Per brief §7 I will **propose** indexes with `EXPLAIN` evidence, not add them.

---

## 4. What I need from you before writing code

1. **Database access** — pick option 1, 2 or 3 from §0.
2. **`analytics_ro` role** — create it, or skip it (it isn't needed for the dashboard
   itself). I have run no DDL.
3. **The 2026-07-06 cutoff** for cohort/funnel/time-to-first metrics — confirm or change.
4. **Signup method split** — the only ❌. Add `owners.sign_in_provider` (migration + write),
   or drop the metric for now? I won't fake it.
5. **WAU definition** — confirm the append-only-writes union above, knowing it misses pure
   edits.
6. **Agent cap hits** — accept the labelled proxy, or persist 429 rejections so the number
   becomes real?
7. **Has `AGENT_GLOBAL_DAILY_COST_LIMIT_USD` ever been changed from 20.0?** If yes, I need
   the dates.

Everything else in §4 of the brief is buildable as specified.

---

## 5. Two notes on later steps

- **Chart library: Chart.js.** It's the smaller of the two, renders the funnel adequately as
  a horizontal stepped bar, and needs no build step. ECharts' extra power (a real funnel
  series, canvas rendering for large datasets) isn't worth ~1MB for eight small charts. Say
  if you'd rather have ECharts.
- **Nothing in §9 (out of scope) is needed.** The 60-second in-memory cache from §5 is
  enough — no Redis, no scheduled process, no warehouse.

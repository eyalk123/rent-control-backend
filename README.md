# RentVance

Property-management software for landlords. Track properties, renters, leases, income and
expenses; get reminders before things fall due; export reports. Built for the Israeli rental
market — Hebrew (RTL) and English throughout, shekel-denominated by default, with rent
escalation linked to the official Consumer Price Index.

This repository is the **backend API**, and it is also the **hub for the whole system** — if you
are new to RentVance, start here. The two client apps are separate repositories and link back
to this document.

---

## Contents

- [What it does](#what-it-does)
- [The system](#the-system) — how the three repos fit together
- [Running everything locally](#running-everything-locally)
- [Backend: setup](#backend-setup)
- [Backend: architecture](#backend-architecture)
- [Environment variables](#environment-variables)
- [Tests](#tests)
- [Deployment](#deployment)
- [Internal analytics dashboard](#internal-analytics-dashboard)

---

## What it does

RentVance is **multi-tenant**: every record belongs to an owner (a landlord), and all data is
scoped to that owner via a verified Firebase ID token. One landlord can never see another's data.

> This README covers the API and how to run it. A separate **product manual** — every feature, how
> users use it, and the full business rules (lease escalation, CPI linkage, reminders, AI lease
> scanning, the chat agent) — lives as `PLATFORM.md` in the workspace folder alongside the three
> repos. It is not checked into this repository.

**Core objects** — properties, renters, transactions (income and expenses), suppliers, expense
categories, and files attached to a property.

**Leases and rent escalation.** A lease can raise rent each year by a fixed percentage, by a
custom per-year rule, or by **linkage to the Israeli Consumer Price Index**. CPI figures are
pulled from the Central Bureau of Statistics' public price-index API (keyless; series `120010`
is the general CPI) and refreshed monthly by a scheduled job.

**AI lease extraction.** Upload a lease as a PDF or DOCX and the backend sends it to Claude,
which extracts the property, the renter, and the lease terms so they can be pre-filled instead of
typed in by hand. PDFs are rasterised page-by-page (pypdfium2) and sent as images, so scanned
leases work too. Extracted values are validated and implausible ones are discarded rather than
trusted — a field that looks wrong is dropped, not guessed. Every extraction is logged with a
token-cost estimate. Requires `ANTHROPIC_API_KEY`; without it the endpoint returns 503 and the
rest of the app is unaffected.

**Portfolio chat agent ("Ask RentVance").** `POST /agent/chat` streams an answer to a
plain-language question about the owner's own data — who is overdue, what a year earned, when a
lease ends. Claude is given **ten read-only tools** (`app/services/agent_tools.py`) and nothing
else: it can query and aggregate, and it cannot create, update or delete anything. Answers cite
the records they came from. Conversations and messages are persisted, and every turn is logged
with an estimated cost so two caps can be enforced — a per-owner daily message limit and a daily
**spend** limit, per owner and app-wide (one message can fan out into several model calls, so
message count alone does not bound spend). Shares `ANTHROPIC_API_KEY` with lease extraction;
without it the endpoints return 503.

**Reminders and push notifications.** Rules generate notifications (for example, rent coming
due), delivered to mobile devices through the Expo Push Service. A daily scheduled job drives
them.

**Reports.** Income/expense summaries and an expense log, exportable as PDF or CSV. PDFs embed
Noto Sans with Noto Sans Hebrew as a fallback (`app/assets/fonts/`, OFL) and reorder RTL text with
`python-bidi`, so Hebrew names and addresses render correctly — fpdf2's built-in Helvetica is
Latin-1 only and raises on the first Hebrew character.

### API surface

| Prefix | What it covers |
|---|---|
| `/properties`, `/renters`, `/transactions` | Core CRUD |
| `/suppliers`, `/expense-categories` | Supporting records |
| `/users` | Owner profile, account deletion, and `me/export` — a ZIP of the owner's records (one .xlsx workbook) plus their uploaded files |
| `/reports` | `income-expense`, `expense-log` (both `?format=pdf\|csv` and `?lang=en\|he` — `he` renders the report in Hebrew, right-to-left), and export `history` |
| `/extract/lease` | AI lease extraction |
| `/agent` | `status`, `chat` (SSE stream), and `conversations` — list, read, delete |
| `/notifications`, `/notification-rules`, `/device-tokens` | Push notifications, the rules that generate them, and device registration. Three event types: `overdue`, `lease_expiring`, `cpi_rent_change` (the last has no rules — mute + materiality threshold instead) |
| `/internal` | `run-reminders`, `run-cpi-indexing` (503 when the CPI cache is stale), `run-lease-generation` (tops up open-ended leases), `run-retention` (`?dry_run=true` supported), and `run-nightly-rollup` (the single Sentry cron check-in for all of them) — cron-triggered, guarded by a shared secret |
| `/support-messages` | The in-app *Report a bug* form: stores the submission and emails it to the product owner via Resend. No read routes — replies go out by email, not into the app |
| `/health` | Unauthenticated liveness check |

Every router except `/internal` and `/health` requires a Firebase ID token. Interactive API docs
are served at `/docs` when the app is running.

---

## The system

Three **independent repositories**, developed side by side:

| Repo | Role | Stack |
|---|---|---|
| [rent-control-backend](https://github.com/eyalk123/rent-control-backend) | This repo — the API | FastAPI, SQLAlchemy, PostgreSQL |
| [rent-control-web](https://github.com/eyalk123/rent-control-web) | Web app | React 19, Vite, TypeScript |
| [rent-control](https://github.com/eyalk123/rent-control) | Mobile app (iOS/Android) | Expo, React Native, TypeScript |

They are not a monorepo — each has its own dependencies, toolchain, and deploy pipeline. Changes
do not propagate between them automatically.

```
   rent-control-web            rent-control (mobile)
          │                             │
          └──────────┬──────────────────┘
                     │  REST + Firebase ID token
                     ▼
            rent-control-backend  ──►  PostgreSQL
                     │
                     ├──►  Firebase (auth + file storage)
                     ├──►  Anthropic Claude (lease extraction + chat agent)
                     ├──►  CBS price-index API (CPI linkage)
                     └──►  Expo Push (notifications)
```

Both clients authenticate against **Firebase** and send the resulting ID token to this API, which
verifies it and derives the owner. Both clients also ship a **mock API** so you can do UI work
with no backend running at all (web: set `VITE_USE_MOCK_API`; mobile: set
`EXPO_PUBLIC_DEV_WEB_PREVIEW=1`, which also stubs Firebase auth so the app runs in a browser).

---

## Running everything locally

Start the backend first — both clients are useless without it (unless you use their mock API).

1. **Backend** — follow [Backend: setup](#backend-setup) below. It listens on `http://localhost:8000`.
2. **Web** — set `VITE_API_URL=http://localhost:8000`, then `npm run dev` (serves on `:5173`).
   Note that `:5173` is the default `CORS_ORIGINS` value here, so it works out of the box.
3. **Mobile** — set `EXPO_PUBLIC_API_URL`, then `npm start`. The correct value depends on where
   the app is running; see the [mobile README](https://github.com/eyalk123/rent-control) (an
   Android emulator cannot reach `localhost`).

---

## Backend: setup

**Prerequisites:** Python 3.11+, a running PostgreSQL, and a Firebase project.

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in the values — see below
alembic upgrade head      # create/update the schema
python run.py             # http://localhost:8000
```

`python run.py` runs without auto-reload. For a reloading dev server use
`uvicorn app.main:app --reload` instead.

Migrations are **not** applied automatically on boot in development — run `alembic upgrade head`
yourself after pulling changes that touch `alembic/versions/`. (Production does run it on every
deploy; see [Deployment](#deployment).)

To create a migration after changing a model:

```bash
alembic revision --autogenerate -m "describe the change"
```

---

## Backend: architecture

```
Router → Service → Repository → Model → PostgreSQL
```

Each domain has a file at each layer, and the layers have strict jobs: routers do HTTP,
**services hold business logic and validation**, **repositories hold every DB query**, models are
the SQLAlchemy tables. Dependencies are wired in `app/api/dependencies.py` with FastAPI
`Depends()`.

| Path | Purpose |
|---|---|
| `app/main.py` | App init, CORS, router registration, `/health` |
| `app/config.py` | Pydantic `Settings` — **the source of truth for env vars** |
| `app/database.py` | Engine, `SessionLocal`, `get_db()` |
| `app/api/dependencies.py` | All DI factories: auth, repositories, services |
| `app/api/routers/` | One file per domain, plus `internal.py` |
| `app/models/` | SQLAlchemy declarative models |
| `app/repositories/` | Data access — all queries live here |
| `app/services/` | Business logic, validation, FK checks |
| `app/schemas/` | Pydantic `Create` / `Update` / `Read` variants per domain |
| `alembic/versions/` | Migration history |

`owner_id` is a string holding the Firebase user id, and it is the multi-tenancy boundary —
repository queries filter on it.

Deeper conventions (DI wiring, the CRUD template, enum and JSON handling, relationship loading)
live in `.claude/docs/architectural_patterns.md`.

---

## Environment variables

`app/config.py` is the source of truth; `.env.example` is a template to copy.

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `FIREBASE_PROJECT_ID` | Yes | Audience for ID-token verification |
| `FIREBASE_STORAGE_BUCKET` | Yes | e.g. `your-project.appspot.com` |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Yes | Full service-account key JSON, as a single-line string |
| `ANTHROPIC_API_KEY` | No | Enables `POST /extract/lease` **and** the chat agent. Empty ⇒ both return 503 |
| `EXTRACTION_MODEL` | No | Default `claude-sonnet-4-6`; use `claude-opus-4-8` if accuracy on hard scans is insufficient |
| `CORS_ORIGINS` | No | Comma-separated browser origins; default `http://localhost:5173`. Mobile is unaffected (CORS is browser-only) |
| `SENTRY_DSN` | No | Sentry error monitoring, backend performance tracing (100% of requests; `/health` excluded) **and** the `nightly-jobs` cron monitor. Empty disables it entirely — no init, no network calls. Set per-environment in Railway |
| `LOG_LEVEL` | No | Root log level; default `INFO`. Without this configuration uvicorn leaves the root logger handler-less and `logger.info` goes nowhere — see `app/logging_config.py` |
| `ENVIRONMENT` | No | Tags Sentry events. Normally leave unset — Railway's injected environment name is used automatically. Set it only to override |
| `DEFAULT_CURRENCY` | No | Default `ILS` |
| `EXPO_ACCESS_TOKEN` | No | Expo Push; only needed with Expo "Enhanced Security" |
| `REMINDER_CRON_SECRET` | No | Guards the `/internal/*` endpoints via the `X-Cron-Secret` header. **Empty disables them** |
| `CBS_API_BASE_URL` | No | Primary CPI source. Default `https://api.cbs.gov.il`. Keyless |
| `CPI_INDEX_ID` | No | **Override** for Israel's configured series id, not the source of truth — the series each country is linked to lives in `app/countries/config.py` (`IndexSeries`). Unset leaves it at Israel's `120010` (general CPI) |
| `BOI_API_BASE_URL` | No | Fallback CPI source (Bank of Israel SDMX). Default `https://edge.boi.gov.il/FusionEdgeServer/sdmx/v2`. Keyless |
| `BOI_CPI_SERIES_CODE` | No | Default `CP` — the same series as `CPI_INDEX_ID` 120010 |
| `CPI_MAX_STALE_MONTHS` | No | Default `2`. How far behind the newest published month the cache may fall before `run-cpi-indexing` returns 503 |
| `PORT` | No | Set by Railway automatically; defaults to 8000 |
| `RESEND_API_KEY` | No | Resend API key for in-app support messages. Empty ⇒ `POST /support-messages` answers 502 (the row is still written). See [Support messages](#support-messages) |
| `RESEND_FROM_ADDRESS` | No | The `From:` address. Must be a domain verified in Resend, or their sandbox sender |
| `SUPPORT_EMAIL_TO` | No | Where support messages land — the product owner's mailbox. Empty disables sending, like an empty key |
| `SUPPORT_MESSAGE_HOURLY_LIMIT` | No | Default `5`. Submissions per owner per rolling hour before `429` |
| `SUPPORT_MESSAGE_MAX_ATTACHMENT_BYTES` | No | Default `6000000`. Combined decoded size of a submission's screenshots |

### Chat agent

All optional — the defaults are sane. Set `ANTHROPIC_API_KEY` and the agent works.

| Variable | Default | Notes |
|---|---|---|
| `AGENT_MODEL` | `claude-sonnet-4-6` | Independent of `EXTRACTION_MODEL` |
| `AGENT_MAX_TOKENS` | `2048` | Tokens per reply |
| `AGENT_MAX_TOOL_ITERS` | `8` | Max model↔tool round-trips per message |
| `AGENT_DAILY_MESSAGE_LIMIT` | `50` | Per owner per calendar day; `429` past it |
| `AGENT_DAILY_COST_LIMIT_USD` | `2.0` | Per owner per UTC day, estimated spend |
| `AGENT_GLOBAL_DAILY_COST_LIMIT_USD` | `20.0` | App-wide daily kill switch; `0` disables |
| `AGENT_RESERVE_COST_USD` | `0.25` | Charged provisionally per turn, reconciled on completion |
| `AGENT_HISTORY_MAX_MESSAGES` | `40` | Recent messages replayed to the model |
| `AGENT_RETENTION_DAYS` | `90` | See Retention below |

### Retention

Data with a shelf life is aged out by a single sweep, `POST /internal/run-retention`. Each class
has its own window and `0` disables that class; the response names the disabled ones so
"scheduled but doing nothing" doesn't look like success.

| Variable | Default | Deletes |
|---|---|---|
| `AGENT_RETENTION_DAYS` | `90` | Chat conversations, by last-updated, with their messages. The shortest window because `agent_messages` holds tenant PII verbatim. Usage logs are detached, not deleted — cost history has no PII |
| `ACTIVITY_LOG_RETENTION_DAYS` | `365` | The deletion trace. Its `label` holds names and addresses, but its job is answering "what happened months ago?", so it outlives the chats |
| `NOTIFICATION_RETENTION_DAYS` | `365` | Sent-notification history. **`cpi_rent_change` is the one exception**: it is kept past the window while the renter's lease is still running (`terminated_on` or `lease_end` in the future), and ages out normally once the lease has ended. It is the only point-in-time record of the amount the owner was shown, and the calculation behind it is not reproducible — leases routinely run longer than a year |
| `CLIENT_USAGE_RETENTION_DAYS` | `365` | `owner_client_days` — which client each owner worked in, per day (see [Which client owners use](#which-client-owners-use)). Counts only, no PII, but owner-scoped behavioural data, and a year answers every question it exists for |

Not swept: `document_extraction_logs` (scanner-quality telemetry, holds no lease content) and
`agent_usage_logs` (cost only). Both are kept indefinitely on purpose.

A window alone does nothing — the job must also be scheduled. See [Deployment](#deployment) for
the dry run to do first.

### Support messages

`POST /support-messages` is the in-app *Report a bug / ask a question / suggest something* form
on both clients (`PLATFORM.md` §14). It writes a row and emails it to `SUPPORT_EMAIL_TO` through
Resend. **There is no admin screen and no in-app inbox** — you reply from your own mail client.

Three things about it are deliberate and easy to break:

* **`Reply-To` is the submitter's address.** Hitting Reply answers the user. Their address is
  never the `From:` — that fails SPF and lands the notification in spam.
* **The email body holds only the user's own words, their name and the type.** Everything
  internal — owner id, message id, client, platform, version, language, country — goes in the
  `diagnostics.txt` attachment, because a mail client quotes the body when you reply and drops
  attachments. Move a field from one to the other and the next routine reply pastes an owner id
  into a customer's mailbox. `tests/test_support_messages.py::test_body_carries_nothing_internal`
  is what guards it.
* **Screenshots are never persisted.** They arrive base64 in the request, go out as attachments
  and are dropped; only a count is stored. A screenshot of this product contains renter PII, so
  the only lasting copy is the one in the recipient's mailbox.

Unlike `push_service`, a failed send is **not** swallowed: the route answers 502 and the client
asks the user to retry. With nobody watching the table, a silently-eaten message would be one
nobody ever reads. The row is committed first either way, so the text survives.

**Setup.** Create a Resend account with the address you want the mail to arrive at, then set the
three variables above. With no verified domain, Resend's sandbox sender only delivers to the
Resend account owner's own address — which is exactly the one recipient this feature has.

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

`pytest.ini` enables coverage by default (`--cov=app --cov-report=term-missing`) and points at
`tests/`.

---

## Deployment

Deployed on **Railway** (Nixpacks), configured in `railway.toml`. Every deploy runs migrations
before booting:

```bash
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Railway health-checks `/health`. Set every required env var in the Railway dashboard, and include
the deployed web origin in `CORS_ORIGINS` or the browser will block the web app.

`run-lease-generation` needs no entry of its own to *work* — `run-reminders` performs it inline when
it has not run that day, the same catch-up `run-cpi-indexing` gets — but giving it one keeps an
open-ended lease's schedule current even if reminders are failing, and is what the nightly rollup
expects to see.

The `/internal/*` jobs are **not** self-scheduling — an external scheduler must call them with the
`X-Cron-Secret` header: `run-reminders`, `run-cpi-indexing`, `run-lease-generation` and
`run-retention`, all daily. (The index itself only updates monthly, but running the job daily costs
one request and picks up a new reading the day it lands.) The Railway cron entries, in UTC — they do
not shift with Israeli DST:

| Job | Schedule (UTC) |
|---|---|
| `run-lease-generation` | `0 2 * * *` |
| `run-cpi-indexing` | `0 3 * * *` |
| `run-retention` | `0 4 * * *` |
| `run-reminders` | `0 9 * * *` |
| `run-nightly-rollup` | `30 9 * * *` |

### Database backups

The production database is dumped daily to a Cloud Storage bucket in the EU by a **separate**
Railway cron service in [`ops/backup/`](ops/backup/README.md) — deliberately sharing no config,
logging or job-tracking with this app, so it can still report a failure caused by the database
being unreachable. That README covers the bucket, the variables, and the restore drill.

### The nightly rollup

A job that is never called writes no row and raises nothing, so only a monitor with an expected
schedule can report it. Sentry's plan includes **one** cron monitor seat for the whole org, so the
three jobs do not check in themselves — three self-declaring monitors meant whichever checked in
first took the seat and the rest were rejected over quota. Instead each job records its run in
`job_runs`, and `run-nightly-rollup` checks in once, half an hour after `run-reminders`, as the
`nightly-jobs` monitor. It reads the last successful run of all three and, if any is missing or
older than 24 hours, sends an error-level Sentry message **naming the stale jobs** and closes the
check-in as errored. Keep the cron entry and `ROLLUP_MONITOR_CONFIG` in
`app/api/routers/internal.py` in sync — Sentry upserts the monitor from the config sent with each
check-in. If the rollup itself never runs, Sentry reports it as a missed check-in.

**Schedule `run-cpi-indexing` before `run-reminders`** — though it is no longer load-bearing. The
indexing job writes the `cpi_rent_change` confirmation as it reprices a lease; the reminders job is
what pushes un-pushed rows. `run-reminders` now checks `job_runs` for a successful indexing run
today and performs it inline if the scheduler hasn't, so either order produces the same result. Both
jobs are idempotent, so the catch-up costs a request and nothing else. Keep the order anyway: it
means the catch-up never fires.

### Switching the onboarding tour on

The guided tour (PLATFORM.md §3) is finished on both clients but ships **off**, behind a master
flag in each app's `src/features/onboarding/flags.ts`. Turning it on is configuration, not a code
change:

- **Web** — set `VITE_ONBOARDING_TOURS=on` as a Railway variable on the web service. The
  `Dockerfile` forwards it into the build. `rentvanceTours(true)` in the browser console is a
  per-browser override that outranks it, for checking a deployed build without switching it on for
  everyone.
- **Mobile** — `EXPO_PUBLIC_ONBOARDING_TOURS=on`. The `preview` and `simulator` EAS profiles
  already set it; `production` deliberately does not.

**Run this once, before the first switch-on:**

```sql
UPDATE owners SET tour_state = '{}';
```

An unfinished version of the tour was briefly live, so some accounts already have progress recorded
against tours they never really saw — those users would silently skip parts of the finished set.
The column records nothing but which onboarding someone has been shown (`tours_seen`,
`seeds_shown`, `tours_disabled`), so the worst case of clearing it is that somebody sees a tour once
more. It is not personal data and there is nothing to preserve.

Note this also clears the `tours_disabled` opt-out, so anyone who had turned tours off gets them
back. That is a handful of accounts at most given the feature was never really live, and Settings
lets them turn it off again — but if that matters, use
`UPDATE owners SET tour_state = jsonb_build_object('tours_disabled', (tour_state::jsonb ->
'tours_disabled'))::text;` instead, which keeps the opt-out and drops both progress maps. Either
statement is safe to read back: `OwnerRepository._decode_tour_state` fills in missing maps and
coerces a null `tours_disabled` to `False`, so a partial blob degrades to "seen nothing".

### Which client owners use

Two clients, one backend. Both used to send only `Content-Type` and `Authorization`, so every
request looked identical to the server and nothing said where owners actually work — which meant
no evidence on which to split effort between mobile and web. `legal_acceptances.platform` records
where someone *signed up* and `device_tokens.platform` where push was registered; neither answers
this.

Both clients now send three headers on every authenticated request:

| Header | Values | Answers |
|---|---|---|
| `X-Client-App` | `mobile` \| `web` | Which product |
| `X-Client-Platform` | `ios` \| `android` \| `web` | Which OS |
| `X-Client-Version` | e.g. `1.4.2`, or a short commit SHA on web | Which build |

`app` and `platform` are separate because the **mobile app can run in a browser** (the Expo web
preview, where `Platform.OS === 'web'`). It still reports `app: mobile`. A single header would
merge that with the real web app and destroy the distinction the whole measurement exists for.

`ClientUsageMiddleware` accumulates them in memory — keyed by `(owner, UTC day, app, platform)` —
and a background task flushes into **`owner_client_days`** once a minute and again on shutdown. No
database write is on the request path. See `app/services/client_usage_service.py` for why the
upsert must *add* rather than set (more than one Railway worker), and why the day is resolved when
the request arrives rather than at flush time (midnight).

Each row carries two counters, and the difference between them is the point: `requests` is
attention, `writes` is work. A five-second app open is `requests: 3, writes: 0`; an evening of
recording payments is `requests: 140, writes: 22`. **Any "where does the work happen" chart is
built on `writes`** — never on row counts or `requests`, both of which score a glance the same as
an evening. Note that an owner active on both clients in a period counts in both, so client shares
of *owners* sum to more than 100%; shares of `writes` do not.

What counts as a write is an explicit allowlist of `(method, path)` pairs, not the HTTP method.
Method alone fails in both directions here: `POST /device-tokens`, `POST /users/me/legal` and
`PATCH /users/me/tour-state` fire automatically on app open and are not work, while the two report
exports are `GET` and are. A new endpoint is ignored until someone opts it in — the safe direction.

```sql
-- Share of real work by client, last 30 days.
select app, sum(writes) as writes, sum(requests) as requests, count(distinct owner_id) as owners
from owner_client_days
where day >= current_date - 30
group by app order by writes desc;
```

The headers are also the reason `CORS_ORIGINS` needs no change but the allowed-header list does:
the browser preflights them, and a CORS config that rejects them fails every web request outright
with nothing in the server log. `allow_headers=["*"]` in `app/main.py` covers them today, and
`tests/test_client_usage.py` preflights them to keep it that way.

### Knowing whether the jobs ran

Every invocation writes a row to **`job_runs`** — `job_name`, `started_at`, `finished_at`, `status`
(`ok` / `degraded` / `stale` / `failed`), the endpoint's own response as `summary`, and `error` when
it raised. Failures are recorded too, because "called and threw" and "never called" both look like
nothing happening and need telling apart.

This exists because the scheduler is external and unobservable: if it stops, retention stops deleting
and nothing else notices. The table holds counts and status flags only — no tenant data — and is
deliberately **not** swept by retention, since the record of whether retention is running has to
outlive retention's own windows. A `?dry_run=true` retention call is not recorded either: it deletes
nothing, and counting it would make an unswept database look swept.

Nothing in the app reads the table — it is for querying (the project's Metabase dashboard reads the
same database). To alert on a stalled scheduler, make the question always return one row, so that
"never ran at all" is loud rather than empty:

```sql
select coalesce(extract(epoch from now() - max(finished_at)) / 3600, 9999) as hours_since_success
from job_runs where job_name = 'retention' and status in ('ok', 'degraded')
```

`run-cpi-indexing` reads the index from **CBS**, falling back to the **Bank of Israel**'s
republication of the same series when CBS is unreachable. CBS is the publisher lease escalation
clauses actually reference, so it is always tried first and its readings supersede the fallback's;
`cpi_index.source` records which feed each cached reading came from. A run served by the fallback
still returns 200 with `degraded: true` — the readings are correct, so nothing is broken.

**It returns 503 when the cache falls more than `CPI_MAX_STALE_MONTHS` behind the newest published
month.** That is the alarm worth wiring up: the fetch itself is best-effort and never raises, so
without this check a completely dead feed keeps returning 200 while every CPI-linked rent silently
stops tracking the index.

Each lease year records the index reading it resolved against (`cpi_reading` inside the `lease_years`
blob, server-owned and invisible to clients). Once a year has *started* and resolved against its own
known-index month it is **frozen** and never recomputed — so a later reading cannot retroactively
change rent a tenant has begun paying. Two exceptions: a year anchored to an older stand-in month
keeps self-healing until the right reading lands, and a source upgrade (CBS superseding a BOI value)
re-derives everything downstream of the provisional figure.

> **Before scheduling `run-retention` for the first time, dry-run it.** The first real run deletes
> everything already older than each window, which on an existing database can be a lot and cannot
> be undone:
>
> ```bash
> curl -X POST -H "X-Cron-Secret: $SECRET" "$API/internal/run-retention?dry_run=true"
> ```
>
> It reports what *would* go, per class, and changes nothing.

The web app also deploys to Railway (Docker + Caddy); the mobile app ships through EAS to the App
Store. See their respective repos.

---

## Internal analytics dashboard

One page, served by this service, showing how the *product* is doing across all users:
signups, activation, retention, the two AI features, and which client owners actually work
in. It replaces a self-hosted Metabase that cost $15/month to sit idle.

It adds **no new service and no new process**. The page is a single static HTML file served
by a route here; the queries run on the connection this app already holds; the 60-second
response cache is a dict in memory. Nothing to deploy, nothing billed per second.

| Piece | Where |
|---|---|
| The SQL — every query, with its caveats | `app/analytics/queries.py` |
| Assembly, caching, the funnel self-check | `app/analytics/service.py` |
| Routes + the admin gate + rate limiting | `app/api/routers/admin.py` |
| The page | `app/static/admin_dashboard.html` |
| What is and isn't measurable, and why | `ANALYTICS_FEASIBILITY.md` |

### Turning it on

Two settings, both empty by default — **an unset `ADMIN_OWNER_IDS` means nobody gets in**,
not "no restriction":

```bash
ADMIN_OWNER_IDS=<your-firebase-uid>          # comma-separated for more than one
FIREBASE_WEB_API_KEY=<the web app's apiKey>  # public client config, not a secret
FIREBASE_WEB_APP_ID=<the web app's appId>
```

The two Firebase values are the same ones `rent-control-web` already ships to every visitor
(`VITE_FIREBASE_API_KEY`, `VITE_FIREBASE_APP_ID`). They let the page's login box sign you in
with your existing account instead of inventing a second password. They are read from the
environment rather than committed, because they are not in this repo today.

To find your UID: it is the `owners.id` of your row (`SELECT id FROM owners WHERE email = …`),
or your user's UID in the Firebase console.

Then open `https://<backend>/admin/dashboard` and sign in.

### How access control works

Four layers, in this order:

1. `GET /admin/dashboard` serves an HTML shell containing **no data at all** — an empty frame
   and a login box, with `X-Robots-Tag: noindex`. Serving it to a stranger discloses nothing.
2. `GET /admin/analytics` requires a valid Firebase ID token in an `Authorization: Bearer`
   header. **Never a token in the query string** — query strings end up in access logs, proxy
   logs, browser history and `Referer`, so a token in one leaks by default.
3. The uid is checked against `ADMIN_OWNER_IDS`. Anyone not on the list gets **404, not 403**:
   a 403 would confirm to the holder of any working account that the route exists and that
   only authorisation stands in the way. Missing token, invalid token and valid-but-not-admin
   are all the same 404.
4. Both routes are rate-limited per IP (`ADMIN_RATE_LIMIT_PER_MINUTE`, default 30), *before*
   authentication — otherwise the route is a free oracle for testing stolen tokens, and every
   attempt costs a Firebase round-trip.

### What the queries cost

Every query is aggregate SQL over a bounded window. The expensive shapes, and what to do if
they ever get slow:

| Query | Shape | If it gets slow |
|---|---|---|
| Weekly active owners | `UNION ALL` over seven tables, grouped | Index `(owner_id, created_at)` on `transactions` |
| Funnel | Four `min(created_at) GROUP BY owner_id` scans, left-joined | Index `owner_id` on `properties` and `renters` |
| Corrected fields by name | Per-row `::jsonb` cast + `jsonb_array_elements` | Make `field_edits` a real `jsonb` column |
| Everything else | Single grouped scan of one small table | — |

**Indexes that do not exist today and that these queries would use:** `properties(owner_id)`,
`renters(owner_id)`, `transactions(owner_id, created_at)`. None have been added — measure
first. The Data Health panel on the dashboard reports the row counts that decide whether it
is worth it.

The whole payload is built in one request and cached for 60 seconds per distinct combination
of range, granularity and country, so refreshing the page does not re-run anything.

### Adding a metric

1. Write the query in `app/analytics/queries.py`. Take `:since` / `:until`, join
   `scoped_owners` so the country filter applies, and **select aggregates only** — no renter
   names, phones, emails or addresses. `tests/test_admin_analytics.py` greps every query for
   PII column names and will fail you if you slip.
2. Add it to the payload in `service.py::build_payload`.
3. Render it in `admin_dashboard.html`: add the card in the section's `*HTML()` function and
   draw it in the matching `draw*()`. Use `chartOrEmpty()` — it renders "no data yet" rather
   than an empty chart that reads as zero.
4. If the number can mislead, add a caveat in `service.py::_caveats()`. It ships with the
   data and renders at the bottom of the page, so a figure is never read without the reason
   it might be wrong.

### What it cannot tell you

Read `ANALYTICS_FEASIBILITY.md` before drawing conclusions. The short version:

- **Data starts 2026-07-06** for anything cohort-shaped. Migration 031 backfilled
  `properties.created_at` and `renters.created_at` to 2026-07-02, and the `owners` table only
  exists from 2026-07-01, so earlier activation figures are an artefact of the migration.
- **Survivor bias everywhere.** Deleting an account erases its rows, so every historical
  metric describes owners who stayed.
- **Two numbers are estimates, and are labelled as such on the page:** assistant cap hits (a
  429 deletes its own usage row, so refusals leave no trace) and lease-scan abandonment (the
  outcome is reported by a client-driven PATCH, so a save whose callback failed reads as
  abandoned).
- **"Active" means an append-only write.** Owners whose week was purely edits are missed,
  because `updated_at` is overwritten in place. Closing that gap is
  `ACTIVITY_LOG_EDIT_LOGGING_PROMPT.md`.

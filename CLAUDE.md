# Rent Control Backend

Property management API for tracking rental properties, renters, and financial transactions.
Multi-tenant: all data is scoped to an authenticated owner via a verified Firebase ID token.

## Tech Stack

- **FastAPI** >=0.109 — web framework, async ASGI
- **SQLAlchemy** 2.0 — ORM (uses `select()` + `scalars()` style)
- **Alembic** — migrations (run before every server start)
- **PostgreSQL** — primary database (via psycopg2-binary)
- **Pydantic** v2 — validation and serialization
- **Firebase** — authentication (ID-token verification against `FIREBASE_PROJECT_ID`) and
  file storage (Firebase Storage)
- **Uvicorn** — ASGI server

## Key Directories

| Path | Purpose |
|---|---|
| `app/main.py` | FastAPI app init, CORS, router registration, `/health` endpoint |
| `app/config.py` | Pydantic `Settings` — reads all env vars from `.env` |
| `app/database.py` | SQLAlchemy engine, `SessionLocal`, `get_db()` dependency |
| `app/api/dependencies.py` | All DI factories: auth, repos, services |
| `app/api/routers/` | One file per domain (properties, renters, transactions, suppliers, expense_categories, users, reports, notifications, notification_preferences, device_tokens, document_extraction, agent) plus `internal.py` (`/health`, `/internal/run-reminders`, `/internal/run-cpi-indexing`, `/internal/run-retention`) |
| `app/models/` | SQLAlchemy declarative models. `activity_log` records deletions (a trace, not a copy — no soft delete anywhere, so reads never need a `deleted_at` filter); `deleted_accounts` is the anonymous tombstone left by account deletion; `job_runs` records every `/internal/*` invocation (status + summary, no tenant data) so a stalled external scheduler is discoverable — never swept by retention |
| `app/repositories/` | Data access layer — all DB queries live here |
| `app/services/` | Business logic — validation, FK checks, transformations. `export_service.py` builds the `GET /users/me/export` archive: an openpyxl workbook (a sheet per record type) plus the owner's Storage files, degrading to workbook-only if Storage is unavailable |
| `app/schemas/` | Pydantic schemas: `Create`, `Update`, `Read` variants per domain |
| `alembic/versions/` | Migration history (`owner_id` is a String holding the auth provider's user id) |

## Commands

```bash
# Development
python run.py                    # starts on $PORT (default 8000), no reload

# Migrations
alembic upgrade head             # apply all pending migrations
alembic revision --autogenerate -m "description"  # generate new migration

# Production (Railway uses this)
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Environment Variables

All env vars are declared in `app/config.py` (`Settings`) — that file is the source of truth.

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `FIREBASE_PROJECT_ID` | Yes | Audience for ID-token verification |
| `FIREBASE_STORAGE_BUCKET` | Yes | e.g. `your-project.appspot.com` |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Yes | Full service-account key JSON as a string |
| `CORS_ORIGINS` | No | Comma-separated allowed browser origins; default `http://localhost:5173` (mobile is unaffected) |
| `SENTRY_DSN` | No | Sentry errors + backend tracing; empty disables it entirely (no init, no network calls) |
| `LOG_LEVEL` | No | Root log level; default `INFO`. See `app/logging_config.py` |
| `ENVIRONMENT` | No | Tags Sentry events. Normally leave unset — Railway's injected environment name is used automatically. Set it only to override |
| `DEFAULT_CURRENCY` | No | Default: `ILS` |
| `EXPO_ACCESS_TOKEN` | No | Expo Push Service; only needed with Expo "Enhanced Security" |
| `REMINDER_CRON_SECRET` | No | Shared secret for all three `POST /internal/*` jobs (`X-Cron-Secret` header); empty disables them. Prefer scheduling `run-cpi-indexing` **before** `run-reminders` — the former writes `cpi_rent_change` rows, the latter pushes them — but it is no longer required: `run-reminders` checks `job_runs` and runs indexing inline if it hasn't run today |
| `CBS_API_BASE_URL` | No | **Primary** CPI source; default `https://api.cbs.gov.il` (CPI rent linkage) |
| `CPI_INDEX_ID` | No | CBS series id for CPI linkage; default `120010` (general Consumer Price Index) |
| `BOI_API_BASE_URL` | No | **Fallback** CPI source (Bank of Israel SDMX); default `https://edge.boi.gov.il/FusionEdgeServer/sdmx/v2` |
| `BOI_CPI_SERIES_CODE` | No | Default `CP` — BOI's republication of the same series as `CPI_INDEX_ID` |
| `CPI_MAX_STALE_MONTHS` | No | Default `2`; past it `run-cpi-indexing` returns 503 instead of a green 200 |
| `ANTHROPIC_API_KEY` | No | Enables **both** `POST /extract/lease` and the chat agent; empty ⇒ both return 503 |
| `EXTRACTION_MODEL` | No | Lease extraction model; default `claude-sonnet-4-6` |
| `PORT` | No | Set by Railway automatically |

### Portfolio chat agent (`/agent`)

`POST /agent/chat` streams SSE. Guarded by two independent layers — a message count and a
**cost** cap — because one message can fan out into several model calls, so counting messages
alone does not bound spend. Costs are summed from `agent_usage_logs.estimated_cost_usd`; a turn
reserves `AGENT_RESERVE_COST_USD` up front and reconciles to its real cost on completion, which
is what makes the caps burst-safe under concurrency.

| Variable | Default | Notes |
|---|---|---|
| `AGENT_MODEL` | `claude-sonnet-4-6` | Independent of `EXTRACTION_MODEL` |
| `AGENT_MAX_TOKENS` | `2048` | Tokens per reply (cost + latency guard) |
| `AGENT_MAX_TOOL_ITERS` | `8` | Max model↔tool round-trips per message; stops a stuck loop |
| `AGENT_DAILY_MESSAGE_LIMIT` | `50` | Per owner per calendar day; 429 past it |
| `AGENT_DAILY_COST_LIMIT_USD` | `2.0` | Per owner per UTC day; the real denial-of-wallet guard |
| `AGENT_GLOBAL_DAILY_COST_LIMIT_USD` | `20.0` | App-wide kill switch; `0` disables it |
| `AGENT_RESERVE_COST_USD` | `0.25` | Provisional charge per turn, reconciled when it finishes |
| `AGENT_HISTORY_MAX_MESSAGES` | `40` | Recent messages replayed to the model |
| `AGENT_RETENTION_DAYS` | `90` | Age-out for conversations. One of three windows swept by `POST /internal/run-retention` (with `ACTIVITY_LOG_RETENTION_DAYS` and `NOTIFICATION_RETENTION_DAYS`, both `365`). `0` disables a class; a window without a scheduled job deletes nothing. `?dry_run=true` counts without deleting |

Tables: `agent_conversations`, `agent_messages`, `agent_usage_logs` (`app/models/agent.py`). All
ten tools in `app/services/agent_tools.py` are **read-only** — the agent answers and cites, it
never writes. `agent_messages` stores replies verbatim, which means tenant PII at rest; account
deletion removes it (`user_service.delete_account`).

## Request Flow

```
Router → Service → Repository → Model → PostgreSQL
```

Each layer has a corresponding file per domain. Dependencies are wired in `app/api/dependencies.py` via FastAPI `Depends()`.

## Monitoring (Sentry)

Configured in `app/monitoring.py`: errors, plus **backend** performance tracing at 100%
of requests. No profiling, no replay; the web and mobile clients stay error-only, which
is what keeps them clear of consent-banner territory (`rent-control-web/DEPLOYMENT_CHECKLIST.md`
B7) and the privacy policies true. Disabled entirely when `SENTRY_DSN` is empty, which is
how the test suite and local development run. Four invariants a future change must not
break:

1. **`init_sentry()` runs before `FastAPI()` is constructed** (`app/main.py`). The
   Starlette integration patches `Starlette.__init__`, so an app built first is never
   wrapped and captures nothing — silently.
2. **HTTP-status-based capture is off** (`failed_request_status_codes=set()`), because
   the app raises `HTTPException(502/503)` deliberately for handled conditions. Any new
   place that converts a real exception into an `HTTPException` must call
   `sentry_sdk.capture_exception(exc)` first, or the cause is lost.
3. **`/health` is not traced** (`_traces_sampler`). It answers every uptime-monitor ping
   and does no work; at a one-minute interval it would be ~43,000 empty transactions a
   month, plausibly more than real traffic. Tracing every real request is only
   affordable because this one is sampled at zero.
4. **The query-string allowlist stays an allowlist** (`_ALLOWED_QUERY_PARAMS`).
   `send_default_pii=False` does *not* cover the query string, and `/transactions` and
   `/suppliers` take a free-text `?q=`. A new query parameter is filtered until someone
   adds it, which is the safe direction.

Only the Firebase UID is attached to events (`get_current_owner`) — never email or name.

**Cron monitors.** `_record()` in `app/api/routers/internal.py` sends a Sentry check-in
around every `/internal/*` job, with the Railway cron schedule declared in
`_CRON_SCHEDULES` (UTC). This is the only thing that can report a job that was *never
called* — no code runs, so nothing raises, and `job_runs` gets no row. Keep
`_CRON_SCHEDULES` in sync with the Railway cron jobs. Expect a second `cpi_indexing`
check-in on days `run-reminders` performs its inline catch-up.

## Logging

Configured in `app/logging_config.py`, called first in `app/main.py`. Uvicorn configures
only its own loggers and leaves the root logger without handlers, so without this
`logger.info` is discarded and `logger.warning` prints a bare message through
`logging.lastResort`. Level comes from `LOG_LEVEL`.

**Never log tenant data** — owner UIDs and row ids only, never renter names, addresses,
phone numbers or lease text. Records at INFO and above also become Sentry breadcrumbs on
error events, so anything logged can leave the box when something else fails.

## Additional Documentation

- `.claude/docs/architectural_patterns.md` — DI wiring, CRUD template, schema conventions, multi-tenancy, enum handling, JSON fields, relationship loading

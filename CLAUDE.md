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
| `app/api/routers/` | One file per domain (properties, renters, transactions, suppliers, expense_categories, users, reports, notifications, notification_preferences, device_tokens, document_extraction, agent) plus `internal.py` (`/health`, `/internal/run-reminders`, `/internal/run-cpi-indexing`, `/internal/run-agent-retention`) |
| `app/models/` | SQLAlchemy declarative models. `activity_log` records deletions (a trace, not a copy — no soft delete anywhere, so reads never need a `deleted_at` filter); `deleted_accounts` is the anonymous tombstone left by account deletion |
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
| `DEFAULT_CURRENCY` | No | Default: `ILS` |
| `EXPO_ACCESS_TOKEN` | No | Expo Push Service; only needed with Expo "Enhanced Security" |
| `REMINDER_CRON_SECRET` | No | Shared secret for all three `POST /internal/*` jobs (`X-Cron-Secret` header); empty disables them |
| `CBS_API_BASE_URL` | No | CBS price-index API base; default `https://api.cbs.gov.il` (CPI rent linkage) |
| `CPI_INDEX_ID` | No | CBS series id for CPI linkage; default `120010` (general Consumer Price Index) |
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
| `AGENT_RETENTION_DAYS` | `0` | Age-out for conversations; **`0` = nothing is deleted**, and only enforced when a scheduler calls `POST /internal/run-agent-retention` |

Tables: `agent_conversations`, `agent_messages`, `agent_usage_logs` (`app/models/agent.py`). All
ten tools in `app/services/agent_tools.py` are **read-only** — the agent answers and cites, it
never writes. `agent_messages` stores replies verbatim, which means tenant PII at rest; account
deletion removes it (`user_service.delete_account`).

## Request Flow

```
Router → Service → Repository → Model → PostgreSQL
```

Each layer has a corresponding file per domain. Dependencies are wired in `app/api/dependencies.py` via FastAPI `Depends()`.

## Additional Documentation

- `.claude/docs/architectural_patterns.md` — DI wiring, CRUD template, schema conventions, multi-tenancy, enum handling, JSON fields, relationship loading

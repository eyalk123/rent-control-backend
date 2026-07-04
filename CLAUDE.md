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
| `app/api/routers/` | One file per domain (properties, renters, transactions, suppliers, expense_categories, users, reports, notifications, notification_preferences, device_tokens) plus `internal.py` (`/health`, `/internal/run-reminders`, `/internal/run-cpi-indexing`) |
| `app/models/` | SQLAlchemy declarative models |
| `app/repositories/` | Data access layer — all DB queries live here |
| `app/services/` | Business logic — validation, FK checks, transformations |
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
| `REMINDER_CRON_SECRET` | No | Shared secret for `POST /internal/run-reminders` and `POST /internal/run-cpi-indexing` (`X-Cron-Secret` header); empty disables them |
| `CBS_API_BASE_URL` | No | CBS price-index API base; default `https://api.cbs.gov.il` (CPI rent linkage) |
| `CPI_INDEX_ID` | No | CBS series id for CPI linkage; default `120010` (general Consumer Price Index) |
| `PORT` | No | Set by Railway automatically |

## Request Flow

```
Router → Service → Repository → Model → PostgreSQL
```

Each layer has a corresponding file per domain. Dependencies are wired in `app/api/dependencies.py` via FastAPI `Depends()`.

## Additional Documentation

- `.claude/docs/architectural_patterns.md` — DI wiring, CRUD template, schema conventions, multi-tenancy, enum handling, JSON fields, relationship loading

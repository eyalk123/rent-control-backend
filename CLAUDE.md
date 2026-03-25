# Rent Control Backend

Property management API for tracking rental properties, renters, and financial transactions.
Multi-tenant: all data is scoped to an authenticated owner via Clerk JWT.

## Tech Stack

- **FastAPI** >=0.109 — web framework, async ASGI
- **SQLAlchemy** 2.0 — ORM (uses `select()` + `scalars()` style)
- **Alembic** — migrations (run before every server start)
- **PostgreSQL** — primary database (via psycopg2-binary)
- **Pydantic** v2 — validation and serialization
- **Clerk** — authentication via JWKS JWT validation (PyJWT + cryptography)
- **Uvicorn** — ASGI server

## Key Directories

| Path | Purpose |
|---|---|
| `app/main.py` | FastAPI app init, CORS, router registration, `/health` endpoint |
| `app/config.py` | Pydantic `Settings` — reads all env vars from `.env` |
| `app/database.py` | SQLAlchemy engine, `SessionLocal`, `get_db()` dependency |
| `app/api/dependencies.py` | All DI factories: auth, repos, services |
| `app/api/routers/` | One file per domain (properties, renters, transactions, suppliers, expense_categories) |
| `app/models/` | SQLAlchemy declarative models |
| `app/repositories/` | Data access layer — all DB queries live here |
| `app/services/` | Business logic — validation, FK checks, transformations |
| `app/schemas/` | Pydantic schemas: `Create`, `Update`, `Read` variants per domain |
| `alembic/versions/` | Migration history (8 migrations, latest: owner_id → String for Clerk) |

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

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `CLERK_JWKS_URL` | Yes | Clerk JWKS endpoint for JWT validation |
| `CLERK_ISSUER` | Yes | Clerk issuer URL |
| `DEFAULT_CURRENCY` | No | Default: `ILS` |
| `S3_BUCKET` | No | Currently mocked; default: `mock-bucket` |
| `PORT` | No | Set by Railway automatically |

## Request Flow

```
Router → Service → Repository → Model → PostgreSQL
```

Each layer has a corresponding file per domain. Dependencies are wired in `app/api/dependencies.py` via FastAPI `Depends()`.

## Additional Documentation

- `.claude/docs/architectural_patterns.md` — DI wiring, CRUD template, schema conventions, multi-tenancy, enum handling, JSON fields, relationship loading

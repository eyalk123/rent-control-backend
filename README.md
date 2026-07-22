# Rent Control

Property-management software for landlords. Track properties, renters, leases, income and
expenses; get reminders before things fall due; export reports. Built for the Israeli rental
market — Hebrew (RTL) and English throughout, shekel-denominated by default, with rent
escalation linked to the official Consumer Price Index.

This repository is the **backend API**, and it is also the **hub for the whole system** — if you
are new to Rent Control, start here. The two client apps are separate repositories and link back
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

---

## What it does

Rent Control is **multi-tenant**: every record belongs to an owner (a landlord), and all data is
scoped to that owner via a verified Firebase ID token. One landlord can never see another's data.

> This README covers the API and how to run it. A separate **product manual** — every feature, how
> users use it, and the full business rules (lease escalation, CPI linkage, reminders, AI lease
> scanning) — lives as `PLATFORM.md` in the workspace folder alongside the three repos. It is not
> checked into this repository.

**Core objects** — properties, renters, transactions (income and expenses), suppliers, expense
categories, and files attached to a property.

**Leases and rent escalation.** A lease can raise rent each year by a fixed percentage, by a
custom per-year rule, or by **linkage to the Israeli Consumer Price Index**. CPI figures are
pulled from the Central Bureau of Statistics' public price-index API (keyless; series `120010`
is the general CPI) and refreshed monthly by a scheduled job.

**AI lease extraction.** Upload a lease as a PDF or DOCX and the backend sends it to Claude,
which extracts the property, the renter, and the lease terms so they can be pre-filled instead of
typed in by hand. PDFs are rasterised page-by-page (PyMuPDF) and sent as images, so scanned
leases work too. Extracted values are validated and implausible ones are discarded rather than
trusted — a field that looks wrong is dropped, not guessed. Every extraction is logged with a
token-cost estimate. Requires `ANTHROPIC_API_KEY`; without it the endpoint returns 503 and the
rest of the app is unaffected.

**Reminders and push notifications.** Rules generate notifications (for example, rent coming
due), delivered to mobile devices through the Expo Push Service. A daily scheduled job drives
them.

**Reports.** Income/expense summaries and an expense log, exportable as PDF.

### API surface

| Prefix | What it covers |
|---|---|
| `/properties`, `/renters`, `/transactions` | Core CRUD |
| `/suppliers`, `/expense-categories` | Supporting records |
| `/users` | Owner profile |
| `/reports` | `income-expense`, `expense-log`, and export `history` |
| `/extract/lease` | AI lease extraction |
| `/notifications`, `/notification-rules`, `/device-tokens` | Push notifications, the rules that generate them, and device registration |
| `/internal` | `run-reminders`, `run-cpi-indexing` — cron-triggered, guarded by a shared secret |
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
                     ├──►  Anthropic Claude (lease extraction)
                     ├──►  CBS price-index API (CPI linkage)
                     └──►  Expo Push (notifications)
```

Both clients authenticate against **Firebase** and send the resulting ID token to this API, which
verifies it and derives the owner. Both clients also ship a **mock API** so you can do UI work
with no backend running at all (web: set `VITE_USE_MOCK_API`; mobile: toggle in
`src/core/api/mock.ts`).

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
| `ANTHROPIC_API_KEY` | No | Enables `POST /extract/lease`. Empty ⇒ that endpoint returns 503 |
| `EXTRACTION_MODEL` | No | Default `claude-sonnet-4-6`; use `claude-opus-4-8` if accuracy on hard scans is insufficient |
| `CORS_ORIGINS` | No | Comma-separated browser origins; default `http://localhost:5173`. Mobile is unaffected (CORS is browser-only) |
| `DEFAULT_CURRENCY` | No | Default `ILS` |
| `EXPO_ACCESS_TOKEN` | No | Expo Push; only needed with Expo "Enhanced Security" |
| `REMINDER_CRON_SECRET` | No | Guards the `/internal/*` endpoints via the `X-Cron-Secret` header. **Empty disables them** |
| `CBS_API_BASE_URL` | No | Default `https://api.cbs.gov.il`. Keyless |
| `CPI_INDEX_ID` | No | Default `120010` (general CPI) |
| `PORT` | No | Set by Railway automatically; defaults to 8000 |

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

The `/internal/*` jobs are **not** self-scheduling — an external scheduler must call them with the
`X-Cron-Secret` header: `run-reminders` daily, `run-cpi-indexing` monthly.

The web app also deploys to Railway (Docker + Caddy); the mobile app ships through EAS to the App
Store. See their respective repos.

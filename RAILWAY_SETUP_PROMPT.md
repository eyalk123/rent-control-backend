# Railway Deployment Setup Guide

## Context
I need to deploy a FastAPI backend to Railway. The project is called **rent-control-backend**. It uses:
- Python / FastAPI
- PostgreSQL database
- Alembic for migrations
- Firebase for authentication (ID-token verification)
- The code is already pushed to GitHub

Please guide me step by step through the Railway dashboard to get this fully deployed.

---

## Step 1 — Create a New Project

1. Go to [railway.app](https://railway.app) and log in
2. Click **"New Project"**
3. Select **"Deploy from GitHub repo"**
4. Find and select the `rent-control-backend` repository
5. Click **"Deploy Now"**

Railway will start building the project automatically using Nixpacks (no Dockerfile needed).

---

## Step 2 — Add a PostgreSQL Database

1. Inside the project, click **"New Service"** (the + button)
2. Select **"Database"**
3. Select **"Add PostgreSQL"**
4. Wait for Railway to provision the database (takes ~30 seconds)

---

## Step 3 — Set Environment Variables on the Backend Service

1. Click on the **backend service** (not the Postgres one)
2. Go to the **"Variables"** tab
3. Add the following environment variables one by one:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `FIREBASE_PROJECT_ID` | *(your Firebase project id)* |
| `FIREBASE_STORAGE_BUCKET` | *(e.g. `your-project.appspot.com`)* |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | *(service-account key JSON, single line)* |
| `CORS_ORIGINS` | *(the deployed web origin)* |
| `DEFAULT_CURRENCY` | `ILS` |

> The full env-var reference — including the optional ones (`ANTHROPIC_API_KEY`,
> `REMINDER_CRON_SECRET`, CPI settings) — is in [`README.md`](README.md). That table is the
> one to trust.

> **How to get the Firebase values:**
> - Firebase Console → **Project Settings → General** for the project id and storage bucket
> - Firebase Console → **Project Settings → Service Accounts → Generate new private key** for
>   `FIREBASE_SERVICE_ACCOUNT_JSON` (paste the whole JSON as a single-line string)

> **Note about `DATABASE_URL`:**
> The value `${{Postgres.DATABASE_URL}}` is a Railway reference variable — it automatically pulls the connection string from the PostgreSQL service you added. Do not replace it with a hardcoded URL.

---

## Step 4 — Verify the Deploy Settings

1. Still on the backend service, go to the **"Settings"** tab
2. Confirm the **Start Command** is either empty (Railway will use `railway.toml`) or set to:
   ```
   alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```
3. Under **"Health Check"**, confirm the path is `/health`

---

## Step 5 — Trigger a Redeploy

1. Go to the **"Deployments"** tab on the backend service
2. Click **"Deploy"** or **"Redeploy"** to apply the new environment variables
3. Watch the build logs — you should see:
   - Nixpacks detecting Python
   - `pip install -r requirements.txt`
   - `alembic upgrade head` running successfully
   - Uvicorn starting on the assigned port

---

## Step 6 — Get the Public URL

1. Go to the backend service **"Settings"** tab
2. Under **"Networking"** → click **"Generate Domain"**
3. Railway will give you a public URL like `https://rent-control-backend-production.up.railway.app`
4. Test it by visiting `https://<your-url>/health` — you should get `{"status": "ok"}`
5. Visit `https://<your-url>/docs` to see the full Swagger API documentation

---

## Checklist

- [ ] Project created from GitHub repo
- [ ] PostgreSQL service added
- [ ] `DATABASE_URL` set as `${{Postgres.DATABASE_URL}}`
- [ ] `FIREBASE_PROJECT_ID` set
- [ ] `FIREBASE_STORAGE_BUCKET` set
- [ ] `FIREBASE_SERVICE_ACCOUNT_JSON` set
- [ ] `CORS_ORIGINS` includes the deployed web origin
- [ ] `DEFAULT_CURRENCY` set to `ILS`
- [ ] Redeployed after setting variables
- [ ] `/health` returns `{"status": "ok"}`
- [ ] Public domain generated

---

## Troubleshooting

**Build fails / can't find packages:**
- Check that `requirements.txt` is in the root of the repo

**`alembic upgrade head` fails:**
- Check that `DATABASE_URL` is correctly set and the Postgres service is running
- Check the Alembic logs for the specific migration error

**401 Unauthorized on all endpoints:**
- Verify `FIREBASE_PROJECT_ID` matches the project the client authenticates against — the ID
  token's audience must equal it
- Make sure your frontend is sending the Firebase ID token as a Bearer token in the
  `Authorization` header

**App crashes on start:**
- Check the deploy logs for Python errors
- Verify all required env vars are set

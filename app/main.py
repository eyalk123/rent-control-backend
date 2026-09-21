import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from app.api.client_usage_middleware import ClientUsageMiddleware
from app.api.dependencies import get_current_owner
from app.api.routers import (
    admin,
    agent,
    countries,
    currencies,
    device_tokens,
    document_extraction,
    subscription,
    webhooks,
    expense_categories,
    internal,
    notification_preferences,
    notifications,
    properties,
    renters,
    reports,
    suppliers,
    support_messages,
    transactions,
    users,
)
from app.config import settings
from app.logging_config import configure_logging
from app.monitoring import init_sentry
from app.services.client_usage_service import recorder

# First, so that anything the rest of this module logs is actually formatted and
# emitted. Uvicorn leaves the root logger without handlers; see app/logging_config.py.
configure_logging()

# Must run before FastAPI() is constructed: the Sentry Starlette integration patches
# Starlette.__init__, so an app built before this call is never wrapped and captures
# nothing — silently.
init_sentry()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Own the client-usage flusher for the life of the process.

    The counters live in memory (see `app/services/client_usage_service.py`), so they need
    both a periodic drain and a final one: Railway sends SIGTERM with a drain period, which
    is long enough for the shutdown flush below to finish. A hard kill loses at most one
    flush interval of telemetry, which is an acceptable trade for keeping a database write
    off the hot path of every request.
    """
    flusher = asyncio.create_task(recorder.run_flush_loop())
    try:
        yield
    finally:
        flusher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await flusher
        # Sync DB work, so off the loop. Swallowed inside flush() — a failure here would
        # otherwise be raised during shutdown, where nothing can act on it.
        await run_in_threadpool(recorder.flush)


app = FastAPI(title="Property Management API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    # Covers the three X-Client-* telemetry headers the web app now sends: on a preflight
    # Starlette echoes the browser's `Access-Control-Request-Headers` back rather than
    # replying with a literal "*", so the wildcard really does allow them. Narrowing this
    # to an explicit list without including `CLIENT_HEADERS` would kill every web request
    # at the preflight, with nothing in the server log to say why — which is why
    # `test_client_usage.py` preflights them against the configured origin.
    allow_headers=["*"],
)

# Outermost, so it sees the final status of every response — including one produced by an
# exception handler. It records nothing unless a route has already resolved an owner id
# onto the request scope, so unauthenticated traffic, CORS preflights, /health and the
# /internal cron jobs all pass straight through.
app.add_middleware(ClientUsageMiddleware)

# Every authenticated router refreshes the owner's profile (throttled) via this dependency.
# `internal` is excluded because it has no Firebase user to refresh — it carries its own
# guard (`verify_cron_secret`, declared on the router itself), so the missing entry here
# means "different auth", not "no auth".
_owner_refresh = [Depends(get_current_owner)]

app.include_router(properties.router, prefix="/properties", tags=["properties"], dependencies=_owner_refresh)
app.include_router(renters.router, prefix="/renters", tags=["renters"], dependencies=_owner_refresh)
app.include_router(transactions.router, prefix="/transactions", tags=["transactions"], dependencies=_owner_refresh)
app.include_router(expense_categories.router, prefix="/expense-categories", tags=["expense-categories"], dependencies=_owner_refresh)
app.include_router(suppliers.router, prefix="/suppliers", tags=["suppliers"], dependencies=_owner_refresh)
app.include_router(users.router, prefix="/users", tags=["users"], dependencies=_owner_refresh)
app.include_router(reports.router, prefix="/reports", tags=["reports"], dependencies=_owner_refresh)
app.include_router(device_tokens.router, prefix="/device-tokens", tags=["device-tokens"], dependencies=_owner_refresh)
app.include_router(notifications.router, prefix="/notifications", tags=["notifications"], dependencies=_owner_refresh)
app.include_router(support_messages.router, prefix="/support-messages", tags=["support-messages"], dependencies=_owner_refresh)
app.include_router(subscription.router, prefix="/subscription", tags=["subscription"], dependencies=_owner_refresh)
app.include_router(notification_preferences.router, tags=["notification-preferences"], dependencies=_owner_refresh)
# Static reference data, and the only routers with no owner refresh: the signup country
# gate runs before there is an account worth refreshing.
app.include_router(countries.router, prefix="/countries", tags=["countries"])
app.include_router(currencies.router, prefix="/currencies", tags=["currencies"])
app.include_router(internal.router, prefix="/internal", tags=["internal"])
# No _owner_refresh: RevenueCat has no Firebase user. The endpoint verifies an HMAC
# signature over the raw body instead — see app/api/routers/webhooks.py.
app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
app.include_router(document_extraction.router, prefix="/extract", tags=["extract"], dependencies=_owner_refresh)
app.include_router(agent.router, prefix="/agent", tags=["agent"], dependencies=_owner_refresh)
# No _owner_refresh: admin.py does its own token check and answers 404 for every
# failure, so the shared dependency would raise 401 first and leak the route.
app.include_router(admin.router, prefix="/admin", tags=["admin"])


@app.get("/health")
def health():
    return {"status": "ok"}

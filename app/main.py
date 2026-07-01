from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dependencies import get_current_owner
from app.api.routers import (
    device_tokens,
    document_extraction,
    expense_categories,
    internal,
    notification_preferences,
    notifications,
    properties,
    renters,
    reports,
    suppliers,
    transactions,
    users,
)
from app.config import settings

app = FastAPI(title="Property Management API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Every authenticated router refreshes the owner's profile (throttled) via this dependency.
# `internal` is excluded — its endpoints are unauthenticated.
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
app.include_router(notification_preferences.router, tags=["notification-preferences"], dependencies=_owner_refresh)
app.include_router(internal.router, prefix="/internal", tags=["internal"])
app.include_router(document_extraction.router, prefix="/extract", tags=["extract"], dependencies=_owner_refresh)


@app.get("/health")
def health():
    return {"status": "ok"}

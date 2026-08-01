import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.api.dependencies import (
    get_cpi_indexing_service,
    get_reminder_service,
    get_retention_service,
)
from app.config import settings
from app.services.cpi_indexing_service import CpiIndexingService
from app.services.reminder_service import ReminderService
from app.services.retention_service import RetentionService

router = APIRouter()


def verify_cron_secret(x_cron_secret: Annotated[str | None, Header()] = None) -> None:
    """Guard internal endpoints with a shared secret instead of user auth, so an
    external scheduler can call them. Uses a constant-time comparison."""
    expected = settings.REMINDER_CRON_SECRET
    if not expected or not x_cron_secret or not secrets.compare_digest(x_cron_secret, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid cron secret")


@router.post("/run-reminders", dependencies=[Depends(verify_cron_secret)])
def run_reminders(
    reminder_service: Annotated[ReminderService, Depends(get_reminder_service)],
):
    """Send overdue-rent and expiring-lease pushes. Intended to be called once a
    day by an external scheduler with the X-Cron-Secret header."""
    sent = reminder_service.run_daily_reminders()
    return {"status": "ok", "sent": sent}


@router.post("/run-cpi-indexing", dependencies=[Depends(verify_cron_secret)])
def run_cpi_indexing(
    cpi_indexing_service: Annotated[CpiIndexingService, Depends(get_cpi_indexing_service)],
):
    """Refresh the cached Consumer Price Index from the CBS API and recompute every
    CPI-linked renter's rent schedule. Intended to be called ~monthly by an external
    scheduler with the X-Cron-Secret header (the index updates monthly)."""
    result = cpi_indexing_service.run_cpi_indexing()
    return {"status": "ok", **result}


@router.post("/run-retention", dependencies=[Depends(verify_cron_secret)])
def run_retention(
    retention_service: Annotated[RetentionService, Depends(get_retention_service)],
    dry_run: bool = False,
):
    """Age out every class of data that has a retention window: chat conversations (whose
    messages hold tenant PII), the deletion trace in activity_log, and sent-notification
    history. Each window is configured separately and `0` disables that class — the response
    lists which are disabled, so "scheduled but doing nothing" doesn't look like success.

    Call daily from an external scheduler with the X-Cron-Secret header.

    `?dry_run=true` reports what *would* be deleted and changes nothing. Worth running before
    enabling a window for the first time: the first real run removes everything already older
    than it, which for an existing database can be a lot.
    """
    return retention_service.run(dry_run=dry_run).as_dict()


@router.post("/run-agent-retention", dependencies=[Depends(verify_cron_secret)])
def run_agent_retention(
    retention_service: Annotated[RetentionService, Depends(get_retention_service)],
):
    """Deprecated alias for `run-retention`, kept so an existing scheduler entry doesn't
    silently stop working. It now sweeps every class, not just the chat agent."""
    return retention_service.run().as_dict()

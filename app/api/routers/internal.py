import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.api.dependencies import get_reminder_service
from app.config import settings
from app.services.reminder_service import ReminderService

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

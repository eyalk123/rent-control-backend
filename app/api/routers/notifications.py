import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import (
    get_current_user,
    get_notification_repository,
    get_reminder_service,
    get_renter_repository,
)
from app.repositories.notification_repository import NotificationRepository
from app.repositories.renter_repository import RenterRepository
from app.schemas.notification import NotificationRead
from app.services.reminder_service import ReminderService

router = APIRouter()


@router.get("", response_model=list[NotificationRead])
def list_notifications(
    current_user: Annotated[dict, Depends(get_current_user)],
    reminder_service: Annotated[ReminderService, Depends(get_reminder_service)],
    notification_repository: Annotated[NotificationRepository, Depends(get_notification_repository)],
    renter_repository: Annotated[RenterRepository, Depends(get_renter_repository)],
    status: Literal["unread", "all"] = "all",
):
    """The in-app feed. Freshens itself on read (persists any newly-due rows, no
    push) so the website stays current without waiting for the daily cron."""
    owner_id = current_user["user_id"]
    reminder_service.generate_for_owner(owner_id)

    result: list[NotificationRead] = []
    for n in notification_repository.list_for_owner(owner_id):
        if status == "unread" and n.read_at is not None:
            continue
        renter = renter_repository.get_by_id(n.entity_id)
        if renter is None:  # renter deleted — skip the dangling notification
            continue
        result.append(NotificationRead(
            id=n.id,
            type=n.type,
            renter_id=n.entity_id,
            first_name=renter.first_name,
            last_name=renter.last_name,
            property_id=renter.property_id,
            property_address=renter.property.address if renter.property else None,
            payment_type=renter.payment_type,
            offset=n.offset,
            data=json.loads(n.data) if n.data else {},
            read=n.read_at is not None,
            dismissed=n.dismissed_at is not None,
            created_at=n.sent_at,
        ))
    return result


@router.post("/read-all", status_code=204)
def mark_all_read(
    current_user: Annotated[dict, Depends(get_current_user)],
    notification_repository: Annotated[NotificationRepository, Depends(get_notification_repository)],
):
    notification_repository.mark_all_read(current_user["user_id"])
    return None


@router.post("/{notification_id}/read", status_code=204)
def mark_read(
    notification_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    notification_repository: Annotated[NotificationRepository, Depends(get_notification_repository)],
):
    if notification_repository.mark_read(notification_id, current_user["user_id"]) is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return None


@router.post("/{notification_id}/dismiss", status_code=204)
def dismiss(
    notification_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    notification_repository: Annotated[NotificationRepository, Depends(get_notification_repository)],
):
    if notification_repository.dismiss(notification_id, current_user["user_id"]) is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return None

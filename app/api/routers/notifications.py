import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import (
    get_current_user,
    get_notification_repository,
    get_reminder_service,
    get_renter_repository,
)
from app.models.notification import Notification, NotificationTypeEnum
from app.repositories.notification_repository import NotificationRepository
from app.repositories.renter_repository import RenterRepository
from app.schemas.notification import NotificationRead
from app.services.reminder_service import ReminderService, _VOLATILE_DATA_KEYS

router = APIRouter()


def _fresh_data(n: Notification, live_data: dict[tuple, dict]) -> dict:
    """The row's stored data with its volatile counts overwritten by the current
    values, when the alert is still live. Preserves everything else (e.g. offset)
    and falls back to the stored data for rows with no live candidate."""
    data = json.loads(n.data) if n.data else {}
    fresh = live_data.get((n.type, n.entity_id, n.period_key))
    if fresh:
        for key in _VOLATILE_DATA_KEYS:
            if key in fresh:
                data[key] = fresh[key]
    return data


def _collapse(rows: list[Notification]) -> list[tuple[Notification, bool]]:
    """Reduce rows sharing (type, renter, period) to the single most-urgent one:
    the latest reminder for overdue (highest offset), the soonest for an expiring
    lease (lowest offset). Returns each winner paired with whether the whole group
    is read (the item stays unread until every offset in it has been seen). Input
    is newest-first; that order is preserved for the winners."""
    groups: dict[tuple, list[Notification]] = {}
    order: list[tuple] = []
    for n in rows:
        key = (n.type, n.entity_id, n.period_key)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(n)

    out: list[tuple[Notification, bool]] = []
    for key in order:
        members = groups[key]
        if key[0] == NotificationTypeEnum.LEASE_EXPIRING:
            winner = min(members, key=lambda m: m.offset)
        else:
            winner = max(members, key=lambda m: m.offset)
        group_read = all(m.read_at is not None for m in members)
        out.append((winner, group_read))
    return out


@router.get("", response_model=list[NotificationRead])
def list_notifications(
    current_user: Annotated[dict, Depends(get_current_user)],
    reminder_service: Annotated[ReminderService, Depends(get_reminder_service)],
    notification_repository: Annotated[NotificationRepository, Depends(get_notification_repository)],
    renter_repository: Annotated[RenterRepository, Depends(get_renter_repository)],
    status: Literal["unread", "all"] = "all",
):
    """The in-app feed. Freshens itself on read (persists any newly-due rows, no
    push) so the website stays current without waiting for the daily cron.

    Multiple offsets for the same (type, renter, period) — e.g. the default
    rent-due offsets [0, 3] — are collapsed to a single, most-urgent item so the
    feed reads as a to-handle list. Push (the cron path) still fires per offset."""
    owner_id = current_user["user_id"]
    generation = reminder_service.generate_for_owner(owner_id)
    # A row's stored counts (days overdue / until expiry) are frozen at creation;
    # refresh them from the live candidate so the feed never shows a stale count.
    live_data = reminder_service.live_data_by_group(generation.candidates)

    representatives = _collapse(notification_repository.list_for_owner(owner_id))

    result: list[NotificationRead] = []
    for n, group_read in representatives:
        if status == "unread" and group_read:
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
            data=_fresh_data(n, live_data),
            read=group_read,
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
    # The feed shows one collapsed item per (type, renter, period); dismissing it
    # (or marking the rent paid) clears every offset in that group at once.
    owner_id = current_user["user_id"]
    row = notification_repository.get_for_owner(notification_id, owner_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    notification_repository.dismiss_group(owner_id, row.type, row.entity_id, row.period_key)
    return None

"""Daily reminder job: turns overdue-rent and expiring-lease state into pushes.

Reuses the existing overdue/expiring computation in ``RenterService`` and the
per-owner ``NotificationLog`` to guarantee one nudge per period, even though the
job is expected to run every day.
"""
import logging
from collections import defaultdict
from datetime import date

from app.config import settings
from app.models.notification_log import NotificationTypeEnum
from app.repositories.device_token_repository import DeviceTokenRepository
from app.repositories.notification_log_repository import NotificationLogRepository
from app.services.notification_messages import (
    normalize_locale,
    render_lease_expiring,
    render_overdue,
)
from app.services.push_service import PushService
from app.services.renter_service import RenterService

logger = logging.getLogger(__name__)


def _renter_label(first_name: str, last_name: str, address: str | None) -> str:
    name = f"{first_name} {last_name}".strip()
    return f"{name} ({address})" if address else name


class ReminderService:
    def __init__(
        self,
        renter_service: RenterService,
        push_service: PushService,
        notification_log_repository: NotificationLogRepository,
        device_token_repository: DeviceTokenRepository,
    ):
        self.renter_service = renter_service
        self.push_service = push_service
        self.notification_log_repository = notification_log_repository
        self.device_token_repository = device_token_repository

    def run_daily_reminders(self) -> dict:
        """Process every owner that has at least one registered device. Builds all
        pushes up front, sends them to Expo in batches with a single fan-out, then
        records dedup state. Returns a summary count of renters notified (after dedup)."""
        owner_ids = self.device_token_repository.list_distinct_owner_ids()
        sent = {"overdue": 0, "lease_expiring": 0}
        messages: list[dict] = []
        # (owner_id, type, entity_id, period_key) tuples to mark_sent once delivered.
        to_mark: list[tuple] = []

        for owner_id in owner_ids:
            tokens_by_locale = self._tokens_by_locale(owner_id)
            if not tokens_by_locale:
                continue
            self._collect_overdue(owner_id, tokens_by_locale, messages, to_mark, sent)
            self._collect_expiring(owner_id, tokens_by_locale, messages, to_mark, sent)

        self.push_service.send_messages(messages)
        # Mark only after the send attempt, preserving "attempted = done" semantics.
        for owner_id, ntype, entity_id, period_key in to_mark:
            self.notification_log_repository.mark_sent(owner_id, ntype, entity_id, period_key)

        logger.info("Daily reminders sent: %s", sent)
        return sent

    def _tokens_by_locale(self, owner_id: str) -> dict[str, list[str]]:
        """Group an owner's device tokens by normalized locale, so each language is
        rendered once. One query per owner (vs. one per renter)."""
        by_locale: dict[str, list[str]] = defaultdict(list)
        for device in self.device_token_repository.list_by_owner(owner_id):
            by_locale[normalize_locale(device.locale)].append(device.token)
        return by_locale

    def _collect_overdue(
        self, owner_id, tokens_by_locale, messages: list[dict], to_mark: list[tuple], sent: dict
    ) -> None:
        period_key = date.today().strftime("%Y-%m")
        for r in self.renter_service.get_overdue_this_month(owner_id=owner_id):
            if self.notification_log_repository.was_sent(
                owner_id, NotificationTypeEnum.OVERDUE, r.renter_id, period_key
            ):
                continue
            label = _renter_label(r.first_name, r.last_name, r.property_address)
            data = {"type": "overdue", "renterId": r.renter_id, "route": f"/renters/{r.renter_id}"}
            for locale, tokens in tokens_by_locale.items():
                title, body = render_overdue(locale, label=label)
                messages.extend(
                    {"to": t, "title": title, "body": body, "data": data} for t in tokens
                )
            to_mark.append((owner_id, NotificationTypeEnum.OVERDUE, r.renter_id, period_key))
            sent["overdue"] += 1

    def _collect_expiring(
        self, owner_id, tokens_by_locale, messages: list[dict], to_mark: list[tuple], sent: dict
    ) -> None:
        expiring = self.renter_service.get_expiring_leases(
            owner_id=owner_id, days_until=settings.LEASE_EXPIRY_REMINDER_DAYS
        )
        for r in expiring:
            period_key = r.lease_end_date.isoformat()
            if self.notification_log_repository.was_sent(
                owner_id, NotificationTypeEnum.LEASE_EXPIRING, r.renter_id, period_key
            ):
                continue
            label = _renter_label(r.first_name, r.last_name, r.property_address)
            data = {
                "type": "lease_expiring",
                "renterId": r.renter_id,
                "route": f"/renters/{r.renter_id}",
            }
            for locale, tokens in tokens_by_locale.items():
                title, body = render_lease_expiring(
                    locale, label=label, days=r.days_until_expiry
                )
                messages.extend(
                    {"to": t, "title": title, "body": body, "data": data} for t in tokens
                )
            to_mark.append(
                (owner_id, NotificationTypeEnum.LEASE_EXPIRING, r.renter_id, period_key)
            )
            sent["lease_expiring"] += 1

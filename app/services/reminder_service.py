"""Daily reminder job: turns overdue-rent and expiring-lease state into pushes.

Reuses the existing overdue/expiring computation in ``RenterService`` and the
per-owner ``NotificationLog`` to guarantee one nudge per period, even though the
job is expected to run every day.
"""
import logging
from datetime import date

from app.config import settings
from app.models.notification_log import NotificationTypeEnum
from app.repositories.device_token_repository import DeviceTokenRepository
from app.repositories.notification_log_repository import NotificationLogRepository
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
        """Process every owner that has at least one registered device. Returns a
        summary count of pushes actually sent (after dedup)."""
        owner_ids = self.device_token_repository.list_distinct_owner_ids()
        sent = {"overdue": 0, "lease_expiring": 0}
        for owner_id in owner_ids:
            sent["overdue"] += self._send_overdue(owner_id)
            sent["lease_expiring"] += self._send_expiring(owner_id)
        logger.info("Daily reminders sent: %s", sent)
        return sent

    def _send_overdue(self, owner_id: str) -> int:
        period_key = date.today().strftime("%Y-%m")
        count = 0
        for r in self.renter_service.get_overdue_this_month(owner_id=owner_id):
            if self.notification_log_repository.was_sent(
                owner_id, NotificationTypeEnum.OVERDUE, r.renter_id, period_key
            ):
                continue
            label = _renter_label(r.first_name, r.last_name, r.property_address)
            self.push_service.send_push(
                owner_id=owner_id,
                title="Rent overdue · שכר דירה באיחור",
                body=f"Rent from {label} is overdue.",
                data={"type": "overdue", "renterId": r.renter_id, "route": f"/renters/{r.renter_id}"},
            )
            self.notification_log_repository.mark_sent(
                owner_id, NotificationTypeEnum.OVERDUE, r.renter_id, period_key
            )
            count += 1
        return count

    def _send_expiring(self, owner_id: str) -> int:
        count = 0
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
            self.push_service.send_push(
                owner_id=owner_id,
                title="Lease expiring · חוזה מסתיים",
                body=f"Lease for {label} expires in {r.days_until_expiry} days.",
                data={
                    "type": "lease_expiring",
                    "renterId": r.renter_id,
                    "route": f"/renters/{r.renter_id}",
                },
            )
            self.notification_log_repository.mark_sent(
                owner_id, NotificationTypeEnum.LEASE_EXPIRING, r.renter_id, period_key
            )
            count += 1
        return count

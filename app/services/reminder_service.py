"""Generates notifications from the rules engine into the feed, and pushes them.

Two entry points share one generation path:
- ``run_daily_reminders`` — the cron job: every owner with renters, persists new
  feed rows, then pushes (Expo) to owners who have it enabled and a device.
- ``generate_for_owner`` — the web feed read: a single owner, persists new rows,
  no push (push stays the cron's job so a page load never spams).

The engine is side-effect free; persistence is deduped here via the feed's
unique key, so the two paths never double-create.
"""
import logging
from collections import defaultdict
from datetime import date

from app.models.notification import Notification
from app.repositories.device_token_repository import DeviceTokenRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.notification_settings_repository import (
    NotificationSettingsRepository,
)
from app.repositories.renter_repository import RenterRepository
from app.services.notification_engine import Candidate, NotificationEngine
from app.services.notification_messages import (
    normalize_locale,
    render_lease_expiring,
    render_overdue,
)
from app.models.notification import NotificationTypeEnum
from app.services.push_service import PushService

logger = logging.getLogger(__name__)


def _renter_label(first_name: str, last_name: str, address: str | None) -> str:
    name = f"{first_name} {last_name}".strip()
    return f"{name} ({address})" if address else name


class ReminderService:
    def __init__(
        self,
        engine: NotificationEngine,
        notification_repository: NotificationRepository,
        settings_repository: NotificationSettingsRepository,
        renter_repository: RenterRepository,
        push_service: PushService,
        device_token_repository: DeviceTokenRepository,
    ):
        self.engine = engine
        self.notification_repository = notification_repository
        self.settings_repository = settings_repository
        self.renter_repository = renter_repository
        self.push_service = push_service
        self.device_token_repository = device_token_repository

    def generate_for_owner(
        self, owner_id: str, today: date | None = None
    ) -> list[tuple[Notification, Candidate]]:
        """Persist any newly-due notifications for one owner (idempotent). Returns
        the rows created this run paired with their candidate (for rendering push)."""
        created: list[tuple[Notification, Candidate]] = []
        for cand in self.engine.evaluate_owner(owner_id, today):
            if self.notification_repository.was_generated(
                owner_id, cand.type, cand.renter_id, cand.period_key, cand.offset
            ):
                continue
            row = self.notification_repository.create(
                owner_id=owner_id,
                type=cand.type,
                entity_id=cand.renter_id,
                period_key=cand.period_key,
                offset=cand.offset,
                data=cand.data,
            )
            created.append((row, cand))
        return created

    def run_daily_reminders(self, today: date | None = None) -> dict:
        """For every owner with renters: generate feed rows, then push those rows
        to owners who have push enabled and a registered device. Returns a summary."""
        summary = {"created": 0, "pushed": 0}
        for owner_id in self.renter_repository.list_distinct_owner_ids():
            created = self.generate_for_owner(owner_id, today)
            summary["created"] += len(created)
            if created:
                summary["pushed"] += self._push(owner_id, created)
        logger.info("Daily reminders: %s", summary)
        return summary

    def _push(self, owner_id: str, created: list[tuple[Notification, Candidate]]) -> int:
        # Master toggle + per-event mute are already applied in evaluate_owner;
        # push simply follows the same on/off as the in-app feed (no separate
        # channel control). Send when the owner has a registered device.
        tokens_by_locale = self._tokens_by_locale(owner_id)
        if not tokens_by_locale:
            return 0

        messages: list[dict] = []
        pushed_ids: list[int] = []
        for row, cand in created:
            data = {
                "type": cand.type.value,
                "renterId": cand.renter_id,
                "route": f"/renters/{cand.renter_id}",
            }
            label = _renter_label(cand.first_name, cand.last_name, cand.property_address)
            for locale, tokens in tokens_by_locale.items():
                title, body = self._render(cand, locale, label)
                messages.extend(
                    {"to": t, "title": title, "body": body, "data": data} for t in tokens
                )
            pushed_ids.append(row.id)

        self.push_service.send_messages(messages)
        self.notification_repository.mark_pushed(pushed_ids)
        return len(pushed_ids)

    @staticmethod
    def _render(cand: Candidate, locale: str, label: str) -> tuple[str, str]:
        if cand.type == NotificationTypeEnum.LEASE_EXPIRING:
            return render_lease_expiring(
                locale, label=label, days=cand.data.get("days_until_expiry", 0)
            )
        return render_overdue(locale, label=label)

    def _tokens_by_locale(self, owner_id: str) -> dict[str, list[str]]:
        """Group an owner's device tokens by normalized locale, so each language is
        rendered once."""
        by_locale: dict[str, list[str]] = defaultdict(list)
        for device in self.device_token_repository.list_by_owner(owner_id):
            by_locale[normalize_locale(device.locale)].append(device.token)
        return by_locale

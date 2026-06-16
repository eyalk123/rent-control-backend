"""CRUD for notification settings and rules, plus the rule preview.

Rules store their list fields (offsets + the three scope arrays) as JSON text;
this service is the boundary that serializes Python lists to/from that storage,
so repositories and the engine deal in plain columns.
"""
import json

from app.models.notification import NotificationTypeEnum
from app.models.notification_rule import NotificationRule
from app.models.notification_settings import NotificationSettings
from app.repositories.notification_rule_repository import NotificationRuleRepository
from app.repositories.notification_settings_repository import (
    NotificationSettingsRepository,
)
from app.services.notification_engine import NotificationEngine

# Rule fields that are stored as JSON text but exposed as lists.
_RULE_LIST_FIELDS = (
    "offsets",
    "scope_property_ids",
    "scope_property_owners",
    "scope_renter_ids",
)


class NotificationPreferencesService:
    def __init__(
        self,
        rule_repository: NotificationRuleRepository,
        settings_repository: NotificationSettingsRepository,
        engine: NotificationEngine,
    ):
        self.rule_repository = rule_repository
        self.settings_repository = settings_repository
        self.engine = engine

    # ── settings ──────────────────────────────────────────────────────────
    def get_settings(self, owner_id: str) -> NotificationSettings:
        return self.settings_repository.get_or_create(owner_id)

    def update_settings(self, owner_id: str, data: dict) -> NotificationSettings:
        update = dict(data)
        if "muted_events" in update and update["muted_events"] is not None:
            update["muted_events"] = json.dumps(update["muted_events"])
        return self.settings_repository.update(owner_id, update)

    # ── rules ─────────────────────────────────────────────────────────────
    def list_rules(self, owner_id: str) -> list[NotificationRule]:
        return self.rule_repository.list_by_owner(owner_id)

    def create_rule(self, owner_id: str, data: dict) -> NotificationRule:
        payload = self._serialize_lists(data)
        rule = NotificationRule(owner_id=owner_id, **payload)
        return self.rule_repository.create(rule)

    def update_rule(self, rule_id: int, owner_id: str, data: dict) -> NotificationRule | None:
        rule = self.rule_repository.get_for_owner(rule_id, owner_id)
        if rule is None:
            return None
        return self.rule_repository.update(rule, self._serialize_lists(data))

    def delete_rule(self, rule_id: int, owner_id: str) -> bool:
        rule = self.rule_repository.get_for_owner(rule_id, owner_id)
        if rule is None:
            return False
        self.rule_repository.delete(rule)
        return True

    # ── preview ───────────────────────────────────────────────────────────
    def preview(
        self,
        owner_id: str,
        event_type: NotificationTypeEnum,
        offsets: list[int],
        property_ids: list[int],
        property_owners: list[str],
        renter_ids: list[int],
    ) -> dict:
        return self.engine.preview_rule(
            owner_id=owner_id,
            event_type=event_type,
            offsets=offsets,
            property_ids=property_ids,
            property_owners=property_owners,
            renter_ids=renter_ids,
        )

    @staticmethod
    def _serialize_lists(data: dict) -> dict:
        """JSON-encode the list-valued rule fields present in ``data``; leave
        scalar fields (event_type, label, enabled) untouched. Drops ``None`` so
        a PATCH only updates supplied fields."""
        out = {}
        for key, value in data.items():
            if value is None:
                continue
            out[key] = json.dumps(value) if key in _RULE_LIST_FIELDS else value
        return out

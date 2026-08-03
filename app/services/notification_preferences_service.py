"""CRUD for notification settings and rules, plus the rule preview.

Rules store their list fields (offsets + the three scope arrays) as JSON text;
this service is the boundary that serializes Python lists to/from that storage,
so repositories and the engine deal in plain columns.
"""
import json

from fastapi import HTTPException

from app.models.notification import NotificationTypeEnum
from app.models.notification_rule import NotificationRule
from app.models.notification_settings import NotificationSettings
from app.repositories.notification_rule_repository import NotificationRuleRepository
from app.repositories.notification_settings_repository import (
    NotificationSettingsRepository,
)
from app.services.notification_engine import RULE_EXEMPT_EVENTS, NotificationEngine

# Rule fields that are stored as JSON text but exposed as lists.
_RULE_LIST_FIELDS = (
    "offsets",
    "scope_property_ids",
    "scope_property_owners",
    "scope_renter_ids",
)

# The WhatsApp message templates an owner may override. Deliberately *not* the
# NotificationTypeEnum values: `cpi_rent_change` reads completely differently in its two
# stages — an estimate that says so, and a settled amount — so each stage gets its own
# template. Mirrored by WHATSAPP_TEMPLATE_KEYS in the clients.
WHATSAPP_TEMPLATE_KEYS = ("overdue", "lease_expiring", "cpi_upcoming", "cpi_changed")
WHATSAPP_TEMPLATE_LOCALES = ("en", "he")
# A wa.me link carries the message in its query string, and long URLs get truncated by
# some launchers before WhatsApp ever sees them. Well above any reasonable message.
WHATSAPP_TEMPLATE_MAX_LEN = 1500


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
        # An explicit `null` would write NULL into a NOT NULL column; treat it the same
        # as omitting the field.
        update = {k: v for k, v in data.items() if v is not None}
        if "muted_events" in update:
            update["muted_events"] = json.dumps(update["muted_events"])
        if "whatsapp_templates" in update:
            update["whatsapp_templates"] = json.dumps(
                self._validate_templates(update["whatsapp_templates"])
            )
        return self.settings_repository.update(owner_id, update)

    @staticmethod
    def _validate_templates(templates: dict) -> dict:
        """Reject an unrecognised template key, locale or oversized body outright.

        Silently dropping them would be worse than a 400: the client would show the
        message as saved and then send the built-in default instead, and the user would
        have no way to tell. Empty and whitespace-only bodies are dropped rather than
        rejected — clearing the box is how the UI expresses "reset this one".
        """
        cleaned: dict[str, dict[str, str]] = {}
        for key, by_locale in templates.items():
            if key not in WHATSAPP_TEMPLATE_KEYS:
                raise HTTPException(
                    status_code=400, detail=f"Unknown message template '{key}'"
                )
            for locale, text in by_locale.items():
                if locale not in WHATSAPP_TEMPLATE_LOCALES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unsupported language '{locale}' for template '{key}'",
                    )
                if len(text) > WHATSAPP_TEMPLATE_MAX_LEN:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Message template '{key}' ({locale}) exceeds "
                        f"{WHATSAPP_TEMPLATE_MAX_LEN} characters",
                    )
                if text.strip():
                    cleaned.setdefault(key, {})[locale] = text
        return cleaned

    # ── rules ─────────────────────────────────────────────────────────────
    def list_rules(self, owner_id: str) -> list[NotificationRule]:
        return self.rule_repository.list_by_owner(owner_id)

    def create_rule(self, owner_id: str, data: dict) -> NotificationRule:
        event = data.get("event_type")
        if event in RULE_EXEMPT_EVENTS:
            # Offsets and scope describe "how long before a date I know about" — a CPI
            # change fires when the index moves, so a rule has nothing to express. The
            # clients hide it; this stops a hand-rolled request creating a rule that
            # would then be silently ignored by the engine.
            raise HTTPException(
                status_code=400,
                detail=f"'{getattr(event, 'value', event)}' is configured by mute and "
                "threshold only, not by rules",
            )
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

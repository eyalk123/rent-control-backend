"""The rules engine: turns a user's notification rules (or the built-in
defaults) into concrete notification candidates for an owner.

A single ``evaluate_owner`` powers both the daily cron (all owners, also sends
push) and the on-read web feed (one owner, persist only). It is side-effect free
— it never writes — so the caller decides what to persist and whether to push.
The shared dedup on the ``Notification`` table collapses overlaps between rules.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import date

from app.models.notification import NotificationTypeEnum
from app.repositories.notification_rule_repository import NotificationRuleRepository
from app.repositories.notification_settings_repository import (
    NotificationSettingsRepository,
)
from app.repositories.renter_repository import RenterRepository
from app.services.renter_service import RenterService

logger = logging.getLogger(__name__)

# Built-in default rules, applied per event only when the user has no custom
# rules for that event. Offsets are days; the event decides before/after.
DEFAULT_RULES: dict[NotificationTypeEnum, list[int]] = {
    NotificationTypeEnum.OVERDUE: [0, 3],          # due day, then +3 days if unpaid
    NotificationTypeEnum.LEASE_EXPIRING: [90, 30],  # 90 and 30 days before lease end
}

EVENT_TYPES = (NotificationTypeEnum.OVERDUE, NotificationTypeEnum.LEASE_EXPIRING)


@dataclass
class RuleSpec:
    """The scope + offsets a rule (or default) evaluates with."""

    offsets: list[int]
    property_ids: list[int] = field(default_factory=list)
    property_owners: list[str] = field(default_factory=list)
    renter_ids: list[int] = field(default_factory=list)


@dataclass
class Candidate:
    """One notification to (maybe) persist + push."""

    type: NotificationTypeEnum
    renter_id: int
    period_key: str
    offset: int
    first_name: str
    last_name: str
    property_address: str | None
    data: dict


def _parse_int_list(raw: str | None) -> list[int]:
    try:
        return [int(x) for x in json.loads(raw or "[]")]
    except (ValueError, TypeError):
        return []


def _parse_str_list(raw: str | None) -> list[str]:
    try:
        return [str(x) for x in json.loads(raw or "[]")]
    except (ValueError, TypeError):
        return []


class NotificationEngine:
    def __init__(
        self,
        rule_repository: NotificationRuleRepository,
        settings_repository: NotificationSettingsRepository,
        renter_service: RenterService,
        renter_repository: RenterRepository,
    ):
        self.rule_repository = rule_repository
        self.settings_repository = settings_repository
        self.renter_service = renter_service
        self.renter_repository = renter_repository

    def evaluate_owner(self, owner_id: str, today: date | None = None) -> list[Candidate]:
        """Resolve effective rules per event and return de-duplicated candidates.
        Returns an empty list if the owner has disabled notifications entirely."""
        today = today or date.today()
        settings = self.settings_repository.get(owner_id)
        if settings is not None and not settings.master_enabled:
            return []
        muted = set(_parse_str_list(settings.muted_events) if settings else [])

        seen: set[tuple] = set()
        candidates: list[Candidate] = []
        for event in EVENT_TYPES:
            if event.value in muted:
                continue
            for spec in self._effective_specs(owner_id, event):
                for cand in self._candidates_for(owner_id, event, spec, today):
                    key = (cand.type, cand.renter_id, cand.period_key, cand.offset)
                    if key in seen:
                        continue
                    seen.add(key)
                    candidates.append(cand)
        return candidates

    def _effective_specs(
        self, owner_id: str, event: NotificationTypeEnum
    ) -> list[RuleSpec]:
        rules = self.rule_repository.list_by_event(owner_id, event, enabled_only=True)
        if rules:
            return [
                RuleSpec(
                    offsets=_parse_int_list(r.offsets),
                    property_ids=_parse_int_list(r.scope_property_ids),
                    property_owners=_parse_str_list(r.scope_property_owners),
                    renter_ids=_parse_int_list(r.scope_renter_ids),
                )
                for r in rules
            ]
        # Fall back to the built-in default for this event (all-properties scope).
        return [RuleSpec(offsets=list(DEFAULT_RULES[event]))]

    def _candidates_for(
        self,
        owner_id: str,
        event: NotificationTypeEnum,
        spec: RuleSpec,
        today: date,
    ) -> list[Candidate]:
        offsets = sorted({o for o in spec.offsets if o >= 0})
        if not offsets:
            return []

        out: list[Candidate] = []
        if event == NotificationTypeEnum.OVERDUE:
            period_key = today.strftime("%Y-%m")
            renters = self.renter_service.get_overdue_this_month(
                owner_id=owner_id,
                property_ids=spec.property_ids,
                property_owners=spec.property_owners,
                renter_ids=spec.renter_ids,
            )
            for r in renters:
                for offset in offsets:
                    # Fire once we're at or past `offset` days overdue (threshold).
                    if r.days_overdue >= offset:
                        out.append(Candidate(
                            type=event,
                            renter_id=r.renter_id,
                            period_key=period_key,
                            offset=offset,
                            first_name=r.first_name,
                            last_name=r.last_name,
                            property_address=r.property_address,
                            data={
                                "days_overdue": r.days_overdue,
                                "amount": r.monthly_amount,
                                "offset": offset,
                            },
                        ))
        elif event == NotificationTypeEnum.LEASE_EXPIRING:
            renters = self.renter_service.get_expiring_leases(
                owner_id=owner_id,
                days_until=max(offsets),
                property_ids=spec.property_ids,
                property_owners=spec.property_owners,
                renter_ids=spec.renter_ids,
            )
            for r in renters:
                period_key = r.lease_end_date.isoformat()
                for offset in offsets:
                    # Fire once the lease is within `offset` days of ending.
                    if r.days_until_expiry <= offset:
                        out.append(Candidate(
                            type=event,
                            renter_id=r.renter_id,
                            period_key=period_key,
                            offset=offset,
                            first_name=r.first_name,
                            last_name=r.last_name,
                            property_address=r.property_address,
                            data={
                                "days_until_expiry": r.days_until_expiry,
                                "offset": offset,
                            },
                        ))
        return out

    def preview_rule(
        self,
        owner_id: str,
        event_type: NotificationTypeEnum,
        offsets: list[int],
        property_ids: list[int] | None = None,
        property_owners: list[str] | None = None,
        renter_ids: list[int] | None = None,
        today: date | None = None,
    ) -> dict:
        """Estimate, without persisting, how many renters a draft rule matches
        and roughly how many reminders it would produce per cycle (one per
        matched renter per offset)."""
        today = today or date.today()
        clean_offsets = sorted({o for o in offsets if o >= 0})
        scope = dict(
            property_ids=property_ids, property_owners=property_owners, renter_ids=renter_ids
        )
        if not clean_offsets:
            return {"matched_renters": 0, "estimated_alerts": 0}

        if event_type == NotificationTypeEnum.LEASE_EXPIRING:
            renters = self.renter_service.get_expiring_leases(
                owner_id=owner_id, days_until=max(clean_offsets), **scope
            )
            matched = len(renters)
        else:  # OVERDUE — any active renter in scope could trigger a rent reminder.
            matched = len(self.renter_repository.get_active(owner_id=owner_id, **scope))

        # One reminder per matched renter per offset (a single rent cycle / lease).
        estimated = matched * len(clean_offsets)
        return {"matched_renters": matched, "estimated_alerts": estimated}

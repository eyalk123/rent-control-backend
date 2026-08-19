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

from app.config import settings as app_settings
from app.models.notification import NotificationTypeEnum
from app.repositories.cpi_index_repository import CpiIndexRepository
from app.repositories.notification_rule_repository import NotificationRuleRepository
from app.repositories.notification_settings_repository import (
    NotificationSettingsRepository,
)
from app.models.notification_settings import (
    DEFAULT_CPI_MIN_CHANGE_AMOUNT,
    DEFAULT_CPI_MIN_CHANGE_PERCENT,
)
from app.repositories.renter_repository import RenterRepository
from app.services import cpi_rent_change as cpi
from app.services.cpi_indexing_service import (
    compute_chained_cpi_amount,
    compute_cpi_amount,
)
from app.services.renter_service import RenterService

logger = logging.getLogger(__name__)

# Built-in default rules, applied per event only when the user has no custom
# rules for that event. Offsets are days; the event decides before/after.
DEFAULT_RULES: dict[NotificationTypeEnum, list[int]] = {
    NotificationTypeEnum.OVERDUE: [0, 3],          # due day, then +3 days if unpaid
    NotificationTypeEnum.LEASE_EXPIRING: [90, 30],  # 90 and 30 days before lease end
    # Not user-editable (see RULE_EXEMPT_EVENTS) — this is the only offset it ever has.
    NotificationTypeEnum.CPI_RENT_CHANGE: [cpi.UPCOMING_OFFSET],
}

EVENT_TYPES = (
    NotificationTypeEnum.OVERDUE,
    NotificationTypeEnum.LEASE_EXPIRING,
    NotificationTypeEnum.CPI_RENT_CHANGE,
)

# Events the rule editor does not cover. "Days before an anchor date" is the only thing
# a rule can express, and a CPI change has no such anchor — it fires when the index
# moves. It is configured by mute + materiality threshold instead.
RULE_EXEMPT_EVENTS = frozenset({NotificationTypeEnum.CPI_RENT_CHANGE})

# Types the feed must not resolve-on-read. See ReminderService._dismiss_resolved.
NON_RESOLVING_EVENTS = frozenset({NotificationTypeEnum.CPI_RENT_CHANGE})


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


def _cpi_thresholds(settings) -> tuple[float, float]:
    """The owner's materiality floor, falling back to the product defaults for an owner
    who has never opened notification settings (no row yet)."""
    if settings is None:
        return DEFAULT_CPI_MIN_CHANGE_AMOUNT, DEFAULT_CPI_MIN_CHANGE_PERCENT
    return (
        settings.cpi_min_change_amount
        if settings.cpi_min_change_amount is not None
        else DEFAULT_CPI_MIN_CHANGE_AMOUNT,
        settings.cpi_min_change_percent
        if settings.cpi_min_change_percent is not None
        else DEFAULT_CPI_MIN_CHANGE_PERCENT,
    )


class NotificationEngine:
    def __init__(
        self,
        rule_repository: NotificationRuleRepository,
        settings_repository: NotificationSettingsRepository,
        renter_service: RenterService,
        renter_repository: RenterRepository,
        cpi_index_repository: CpiIndexRepository | None = None,
    ):
        self.rule_repository = rule_repository
        self.settings_repository = settings_repository
        self.renter_service = renter_service
        self.renter_repository = renter_repository
        # Optional: without it the CPI heads-up simply produces nothing, which is the
        # right degradation — an estimate with no index behind it is worse than silence.
        self.cpi_index_repository = cpi_index_repository

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
                for cand in self._candidates_for(owner_id, event, spec, today, settings):
                    key = (cand.type, cand.renter_id, cand.period_key, cand.offset)
                    if key in seen:
                        continue
                    seen.add(key)
                    candidates.append(cand)
        return candidates

    def active_event_types(self, owner_id: str) -> set[NotificationTypeEnum]:
        """The event types currently in effect for the owner: empty when
        notifications are disabled account-wide, otherwise every built-in event
        except the muted ones. Mirrors the gating in ``evaluate_owner`` so the
        feed's resolve-on-read pass can tell 'condition resolved' (absent from
        candidates) apart from 'event muted / disabled' (never evaluated)."""
        settings = self.settings_repository.get(owner_id)
        if settings is not None and not settings.master_enabled:
            return set()
        muted = set(_parse_str_list(settings.muted_events) if settings else [])
        return {e for e in EVENT_TYPES if e.value not in muted}

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
        settings=None,
    ) -> list[Candidate]:
        offsets = sorted({o for o in spec.offsets if o >= 0})
        if not offsets:
            return []

        out: list[Candidate] = []
        if event == NotificationTypeEnum.CPI_RENT_CHANGE:
            return self._cpi_candidates(owner_id, spec, today, settings)
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

    # ── CPI rent change: the heads-up half ────────────────────────────────
    #
    # Only the heads-up is generated here. The confirmation is emitted by the indexing
    # job, because by the time this engine could look, the old amount is already gone.

    def _cpi_candidates(
        self, owner_id: str, spec: RuleSpec, today: date, settings
    ) -> list[Candidate]:
        """Renters whose next CPI repricing lands within the lead window, with an
        estimate of what the new rent will be."""
        if self.cpi_index_repository is None:
            return []
        # The anniversary's own index isn't published yet — that is the whole reason
        # this stage is an estimate. The newest reading known today is the best proxy.
        proxy = self.cpi_index_repository.latest_on_or_before(
            app_settings.CPI_INDEX_ID, today
        )
        if proxy is None:
            return []
        min_amount, min_percent = _cpi_thresholds(settings)

        out: list[Candidate] = []
        for r in self.renter_repository.get_active(
            owner_id=owner_id,
            property_ids=spec.property_ids,
            property_owners=spec.property_owners,
            renter_ids=spec.renter_ids,
        ):
            years = cpi.parse_lease_years(r.lease_years)
            if not cpi.is_cpi_linked(r.rent_escalation_mode, years):
                continue
            current = cpi.lease_year_index(r.lease_start, years, today)
            if current is None:
                continue
            nxt = current + 1
            if nxt >= len(years):
                continue  # the lease runs out before another repricing

            anniversary = cpi.anniversary_of(r.lease_start, years, nxt)
            days_until = (anniversary - today).days
            if not 0 <= days_until <= cpi.UPCOMING_OFFSET:
                continue

            old_amount = years[current].get("amount")
            estimate = self._estimate_repriced_amount(r, years, current, nxt, proxy, today)
            if estimate is None or old_amount is None:
                continue
            if not cpi.is_material(old_amount, estimate, min_amount, min_percent):
                continue

            out.append(
                Candidate(
                    type=NotificationTypeEnum.CPI_RENT_CHANGE,
                    renter_id=r.id,
                    period_key=anniversary.isoformat(),
                    offset=cpi.UPCOMING_OFFSET,
                    first_name=r.first_name,
                    last_name=r.last_name,
                    property_address=r.property.address if r.property else None,
                    data=cpi.build_data(
                        cpi.STAGE_UPCOMING,
                        old_amount=old_amount,
                        new_amount=estimate,
                        effective_date=anniversary,
                        year_index=nxt,
                        base_index=r.cpi_base_index,
                        known_index=proxy,
                    ),
                )
            )
        return out

    def _estimate_repriced_amount(
        self, renter, years: list[dict], current: int, nxt: int, proxy: float, today: date
    ) -> float | None:
        """What the next lease year would come to if the index stopped moving today.

        Uses the same two linkage models the job does, so the estimate and the eventual
        confirmation are computed the same way and differ only by which reading was
        available."""
        if renter.rent_escalation_mode == "cpi":
            base_rent = renter.base_rent or years[0].get("amount")
            if base_rent is None:
                return None
            return compute_cpi_amount(base_rent, renter.cpi_base_index, proxy)

        # `custom`: only a CPI-ruled year is repriced by the index; anything else was
        # already final when the owner typed it, so there is nothing to warn about.
        if cpi.year_rule_mode(years[nxt]) != "cpi":
            return None
        prev_amount = years[current].get("amount")
        if prev_amount is None:
            return None
        prev_index = self.cpi_index_repository.latest_on_or_before(
            app_settings.CPI_INDEX_ID,
            cpi.anniversary_of(renter.lease_start, years, current),
        )
        return compute_chained_cpi_amount(prev_amount, prev_index, proxy)

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
        if not clean_offsets or event_type in RULE_EXEMPT_EVENTS:
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

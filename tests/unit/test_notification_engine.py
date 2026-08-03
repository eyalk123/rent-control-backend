"""Unit tests for the notification rules engine (real repos over the test session)."""
import json
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from freezegun import freeze_time

from app.config import settings
from app.models.notification import NotificationTypeEnum
from app.repositories.cpi_index_repository import CpiIndexRepository, reference_period
from app.repositories.notification_rule_repository import NotificationRuleRepository
from app.repositories.notification_settings_repository import (
    NotificationSettingsRepository,
)
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.models.notification_rule import NotificationRule
from app.services.notification_engine import NotificationEngine
from app.services.renter_service import RenterService
from tests.conftest import OWNER_A
from tests.factories import make_property, make_renter

TODAY = date(2026, 6, 15)


def _engine(db_session) -> NotificationEngine:
    renter_repo = RenterRepository(db_session)
    return NotificationEngine(
        rule_repository=NotificationRuleRepository(db_session),
        settings_repository=NotificationSettingsRepository(db_session),
        renter_service=RenterService(renter_repo, PropertyRepository(db_session)),
        renter_repository=renter_repo,
    )


def _cpi_engine(db_session) -> NotificationEngine:
    """The engine with the index cache wired — without it the CPI heads-up is a no-op,
    which is what every other test in this file relies on."""
    renter_repo = RenterRepository(db_session)
    return NotificationEngine(
        rule_repository=NotificationRuleRepository(db_session),
        settings_repository=NotificationSettingsRepository(db_session),
        renter_service=RenterService(renter_repo, PropertyRepository(db_session)),
        renter_repository=renter_repo,
        cpi_index_repository=CpiIndexRepository(db_session),
    )


def _seed_index(db_session, value: float):
    """A reading for the month that is *known* on TODAY, so it is what the heads-up
    picks up as its proxy for the not-yet-published anniversary index."""
    year, month = reference_period(TODAY)
    CpiIndexRepository(db_session).upsert_many(
        settings.CPI_INDEX_ID, [(year, month, value)], source="cbs"
    )


def _make_cpi_renter(db_session, property_id, *, anniversary_in_days=16, years=3, **kw):
    """A CPI lease whose *next* anniversary lands ``anniversary_in_days`` from TODAY."""
    lease_start = TODAY + timedelta(days=anniversary_in_days) - relativedelta(years=1)
    kw.setdefault("rent_escalation_mode", "cpi")
    return make_renter(
        db_session,
        property_id=property_id,
        lease_start=lease_start,
        lease_end=lease_start + relativedelta(years=years),
        base_rent=5000.0,
        cpi_base_index=100.0,
        payment_day_of_month=28,  # not yet due -> no overdue noise
        lease_years=[{"amount": 5000.0, "type": "contract"} for _ in range(years)],
        **kw,
    )


def _cpi_candidates(db_session):
    return [
        c
        for c in _cpi_engine(db_session).evaluate_owner(OWNER_A, TODAY)
        if c.type == NotificationTypeEnum.CPI_RENT_CHANGE
    ]


def _make_overdue(db_session, property_id, payment_day=14, **kw):
    """A renter whose payment day has passed this month with no revenue recorded."""
    return make_renter(
        db_session,
        property_id=property_id,
        lease_start=TODAY - timedelta(days=100),
        lease_end=TODAY + timedelta(days=300),
        payment_day_of_month=payment_day,
        **kw,
    )


def _make_expiring(db_session, property_id, days_out=40, **kw):
    kw.setdefault("payment_day_of_month", 28)  # not yet due -> not also overdue
    return make_renter(
        db_session,
        property_id=property_id,
        lease_start=TODAY - timedelta(days=300),
        lease_end=TODAY + timedelta(days=days_out),
        **kw,
    )


@freeze_time("2026-06-15")
def test_defaults_fire_when_no_rules(db_session):
    prop = make_property(db_session)
    _make_overdue(db_session, prop.id, payment_day=14)  # 1 day overdue -> offset 0 only
    _make_expiring(db_session, prop.id, days_out=40)    # 40 days out -> offset 90 only

    cands = _engine(db_session).evaluate_owner(OWNER_A, TODAY)

    by_type = {c.type: c for c in cands}
    assert set(by_type) == {NotificationTypeEnum.OVERDUE, NotificationTypeEnum.LEASE_EXPIRING}
    assert by_type[NotificationTypeEnum.OVERDUE].offset == 0
    assert by_type[NotificationTypeEnum.LEASE_EXPIRING].offset == 90


@freeze_time("2026-06-15")
def test_null_payment_day_fires_overdue_from_the_first(db_session):
    prop = make_property(db_session)
    _make_overdue(db_session, prop.id, payment_day=None)  # no payment day -> due on the 1st

    overdue = [c for c in _engine(db_session).evaluate_owner(OWNER_A, TODAY)
               if c.type == NotificationTypeEnum.OVERDUE]
    assert sorted(c.offset for c in overdue) == [0, 3]  # 14 days overdue (since the 1st)


@freeze_time("2026-06-15")
def test_multiple_offsets_fire_on_threshold(db_session):
    prop = make_property(db_session)
    _make_overdue(db_session, prop.id, payment_day=1)  # 14 days overdue -> offsets 0 and 3

    overdue = [c for c in _engine(db_session).evaluate_owner(OWNER_A, TODAY)
               if c.type == NotificationTypeEnum.OVERDUE]
    assert sorted(c.offset for c in overdue) == [0, 3]


@freeze_time("2026-06-15")
def test_custom_rule_replaces_default_for_that_event_only(db_session):
    prop = make_property(db_session)
    _make_expiring(db_session, prop.id, days_out=40)
    # A custom lease_expiring rule with a tighter window: 40 days out should NOT fire.
    db_session.add(NotificationRule(
        owner_id=OWNER_A,
        event_type=NotificationTypeEnum.LEASE_EXPIRING,
        offsets=json.dumps([10]),
    ))
    db_session.commit()

    cands = _engine(db_session).evaluate_owner(OWNER_A, TODAY)
    assert [c for c in cands if c.type == NotificationTypeEnum.LEASE_EXPIRING] == []


@freeze_time("2026-06-15")
def test_muted_event_is_suppressed(db_session):
    prop = make_property(db_session)
    _make_overdue(db_session, prop.id, payment_day=14)
    NotificationSettingsRepository(db_session).update(
        OWNER_A, {"muted_events": json.dumps(["overdue"])}
    )

    cands = _engine(db_session).evaluate_owner(OWNER_A, TODAY)
    assert [c for c in cands if c.type == NotificationTypeEnum.OVERDUE] == []


@freeze_time("2026-06-15")
def test_master_disabled_returns_nothing(db_session):
    prop = make_property(db_session)
    _make_overdue(db_session, prop.id, payment_day=14)
    NotificationSettingsRepository(db_session).update(OWNER_A, {"master_enabled": False})

    assert _engine(db_session).evaluate_owner(OWNER_A, TODAY) == []


@freeze_time("2026-06-15")
def test_scope_filters_by_property_owner(db_session):
    cohen = make_property(db_session, property_owner="Cohen")
    levi = make_property(db_session, property_owner="Levi")
    _make_overdue(db_session, cohen.id, payment_day=14, first_name="C")
    _make_overdue(db_session, levi.id, payment_day=14, first_name="L")
    db_session.add(NotificationRule(
        owner_id=OWNER_A,
        event_type=NotificationTypeEnum.OVERDUE,
        offsets=json.dumps([0]),
        scope_property_owners=json.dumps(["Cohen"]),
    ))
    db_session.commit()

    overdue = [c for c in _engine(db_session).evaluate_owner(OWNER_A, TODAY)
               if c.type == NotificationTypeEnum.OVERDUE]
    assert [c.first_name for c in overdue] == ["C"]


@freeze_time("2026-06-15")
def test_overlapping_rules_are_deduplicated(db_session):
    prop = make_property(db_session)
    _make_overdue(db_session, prop.id, payment_day=14)
    for _ in range(2):  # two identical rules both match the same renter at offset 0
        db_session.add(NotificationRule(
            owner_id=OWNER_A,
            event_type=NotificationTypeEnum.OVERDUE,
            offsets=json.dumps([0]),
        ))
    db_session.commit()

    overdue = [c for c in _engine(db_session).evaluate_owner(OWNER_A, TODAY)
               if c.type == NotificationTypeEnum.OVERDUE]
    assert len(overdue) == 1


@freeze_time("2026-06-15")
def test_preview_counts_matched_and_estimated(db_session):
    prop = make_property(db_session)
    _make_expiring(db_session, prop.id, days_out=40)

    result = _engine(db_session).preview_rule(
        owner_id=OWNER_A,
        event_type=NotificationTypeEnum.LEASE_EXPIRING,
        offsets=[90, 30],
    )
    assert result["matched_renters"] == 1
    assert result["estimated_alerts"] == 2  # both offsets land within the 90-day horizon


# --- CPI rent change: the heads-up stage -------------------------------------
#
# Only the heads-up is the engine's job. The confirmation is emitted by the indexing
# job (see tests/test_cpi_indexing.py) because the old amount exists only at that
# instant.


@freeze_time("2026-06-15")
def test_cpi_heads_up_fires_inside_the_lead_window(db_session):
    prop = make_property(db_session)
    _seed_index(db_session, 110.0)  # +10% since the base index of 100
    _make_cpi_renter(db_session, prop.id, anniversary_in_days=16)

    cands = _cpi_candidates(db_session)

    assert len(cands) == 1
    assert cands[0].offset == 30
    assert cands[0].period_key == (TODAY + timedelta(days=16)).isoformat()
    assert cands[0].data["stage"] == "upcoming"
    assert cands[0].data["old_amount"] == 5000.0
    assert cands[0].data["new_amount"] == 5500  # 5000 x 110/100


@freeze_time("2026-06-15")
def test_cpi_heads_up_silent_outside_the_lead_window(db_session):
    prop = make_property(db_session)
    _seed_index(db_session, 110.0)
    _make_cpi_renter(db_session, prop.id, anniversary_in_days=40)

    assert _cpi_candidates(db_session) == []


@freeze_time("2026-06-15")
def test_cpi_heads_up_respects_the_materiality_threshold(db_session):
    prop = make_property(db_session)
    # +0.3% -> ₪15 on ₪5,000, under the 0.5% (₪25) floor.
    _seed_index(db_session, 100.3)
    _make_cpi_renter(db_session, prop.id, anniversary_in_days=16)

    assert _cpi_candidates(db_session) == []


@freeze_time("2026-06-15")
def test_cpi_heads_up_honours_a_lowered_threshold(db_session):
    prop = make_property(db_session)
    _seed_index(db_session, 100.3)
    _make_cpi_renter(db_session, prop.id, anniversary_in_days=16)
    NotificationSettingsRepository(db_session).update(
        OWNER_A, {"cpi_min_change_amount": 5.0, "cpi_min_change_percent": 0.1}
    )

    assert len(_cpi_candidates(db_session)) == 1


@freeze_time("2026-06-15")
def test_cpi_heads_up_is_muteable(db_session):
    prop = make_property(db_session)
    _seed_index(db_session, 110.0)
    _make_cpi_renter(db_session, prop.id, anniversary_in_days=16)
    NotificationSettingsRepository(db_session).update(
        OWNER_A, {"muted_events": json.dumps(["cpi_rent_change"])}
    )

    assert _cpi_candidates(db_session) == []


@freeze_time("2026-06-15")
def test_cpi_heads_up_silent_on_the_final_lease_year(db_session):
    """Nothing left to reprice — the lease ends before another anniversary."""
    prop = make_property(db_session)
    _seed_index(db_session, 110.0)
    _make_cpi_renter(db_session, prop.id, anniversary_in_days=16, years=1)

    assert _cpi_candidates(db_session) == []


@freeze_time("2026-06-15")
def test_cpi_heads_up_ignores_non_indexed_leases(db_session):
    prop = make_property(db_session)
    _seed_index(db_session, 110.0)
    _make_cpi_renter(
        db_session, prop.id, anniversary_in_days=16, rent_escalation_mode="percent"
    )

    assert _cpi_candidates(db_session) == []


@freeze_time("2026-06-15")
def test_cpi_rules_are_not_previewable(db_session):
    """The event has no rule editor, so a preview must not imply one exists."""
    prop = make_property(db_session)
    _seed_index(db_session, 110.0)
    _make_cpi_renter(db_session, prop.id, anniversary_in_days=16)

    result = _cpi_engine(db_session).preview_rule(
        owner_id=OWNER_A,
        event_type=NotificationTypeEnum.CPI_RENT_CHANGE,
        offsets=[30],
    )
    assert result == {"matched_renters": 0, "estimated_alerts": 0}

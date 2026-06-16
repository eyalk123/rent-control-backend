"""Unit tests for the notification rules engine (real repos over the test session)."""
import json
from datetime import date, timedelta

from freezegun import freeze_time

from app.models.notification import NotificationTypeEnum
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

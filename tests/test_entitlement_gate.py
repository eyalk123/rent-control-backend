"""The gate: grandfather grants, the 402, and what "read-only over the limit" means.

Two things here matter more than the rest.

**Enforcement is off by default.** Every test that expects a refusal has to turn
`ENTITLEMENT_ENFORCED` on explicitly, and one test asserts the default leaves writes
alone — because shipping the gate hot would cap every existing landlord at two
properties with no plan available to buy.

**The locked set is stable.** It is the tail of the account's properties ordered oldest
first, and it must not move because something unrelated was edited. That is the whole
reason the selection is deterministic rather than random.
"""
import pytest

from app.config import settings
from app.models.owner import Owner
from app.models.property import Property, PropertyTypeEnum
from app.repositories.subscription_repository import SubscriptionRepository
from app.services import entitlement_service as ent
from app.services.entitlement_gate import EntitlementGate
from tests.conftest import OWNER_A

from datetime import datetime, timedelta

LATER = datetime(2026, 12, 31)


@pytest.fixture
def enforced():
    """Turn the gate on for one test, and put it back afterwards."""
    original = settings.ENTITLEMENT_ENFORCED
    settings.ENTITLEMENT_ENFORCED = True
    yield
    settings.ENTITLEMENT_ENFORCED = original


def _owner(db, owner_id=OWNER_A, granted_plan=None):
    owner = db.get(Owner, owner_id)
    if owner is None:
        owner = Owner(id=owner_id, email="a@b.c")
        db.add(owner)
    owner.granted_plan = granted_plan
    db.commit()
    return owner


def _properties(db, count, owner_id=OWNER_A):
    """`count` properties, created in ascending time order so "oldest" is unambiguous."""
    base = datetime(2026, 1, 1, 12, 0, 0)
    made = []
    for i in range(count):
        prop = Property(
            owner_id=owner_id,
            address=f"{i + 1} Test St",
            city="Testville",
            zip_code="00000",
            type=PropertyTypeEnum.APARTMENT,
            sq_ft=80,
            purchase_price=0,
            created_at=base + timedelta(days=i),
        )
        db.add(prop)
        made.append(prop)
    db.commit()
    for prop in made:
        db.refresh(prop)
    return made


# ── Grandfathering ───────────────────────────────────────────────────────────


def test_granted_plan_applies_with_no_subscription_at_all(db_session):
    """The whole point of the grant: no payment, no subscription row, top tier anyway."""
    _owner(db_session, granted_plan=ent.PLAN_TIER_16_PLUS)
    _properties(db_session, 30)

    state = EntitlementGate(db_session).state_for(OWNER_A)

    assert state.plan.plan == ent.PLAN_TIER_16_PLUS
    assert state.locked_property_ids == []
    assert state.over_limit is False


def test_grant_survives_a_cancelled_subscription(db_session):
    """The regression this design exists to prevent. A grandfathered landlord who
    subscribed and later cancelled must not drop to free — the grant was never
    conditional on paying for anything."""
    _owner(db_session, granted_plan=ent.PLAN_TIER_16_PLUS)
    SubscriptionRepository(db_session).upsert(
        OWNER_A,
        plan=ent.PLAN_TIER_3_8,
        period="monthly",
        status="expired",
        source="paddle",
    )
    _properties(db_session, 20)

    state = EntitlementGate(db_session).state_for(OWNER_A)
    assert state.plan.plan == ent.PLAN_TIER_16_PLUS
    assert state.locked_property_ids == []


def test_a_paid_plan_smaller_than_the_grant_does_not_shrink_access(db_session):
    _owner(db_session, granted_plan=ent.PLAN_TIER_16_PLUS)
    SubscriptionRepository(db_session).upsert(
        OWNER_A,
        plan=ent.PLAN_TIER_3_8,
        period="monthly",
        status="active",
        source="apple",
        current_period_end=LATER,
    )
    assert (
        EntitlementGate(db_session).state_for(OWNER_A).plan.plan == ent.PLAN_TIER_16_PLUS
    )


def test_a_paid_plan_larger_than_the_grant_wins(db_session):
    _owner(db_session, granted_plan=ent.PLAN_TIER_3_8)
    SubscriptionRepository(db_session).upsert(
        OWNER_A,
        plan=ent.PLAN_TIER_16_PLUS,
        period="yearly",
        status="active",
        source="paddle",
        current_period_end=LATER,
    )
    assert (
        EntitlementGate(db_session).state_for(OWNER_A).plan.plan == ent.PLAN_TIER_16_PLUS
    )


def test_no_grant_and_no_subscription_is_the_free_plan(db_session):
    _owner(db_session, granted_plan=None)
    assert EntitlementGate(db_session).state_for(OWNER_A).plan.plan == ent.PLAN_FREE


# ── Which properties lock ────────────────────────────────────────────────────


def test_the_oldest_stay_writable_and_the_excess_locks(db_session):
    _owner(db_session, granted_plan=None)
    props = _properties(db_session, 5)

    state = EntitlementGate(db_session).state_for(OWNER_A)

    assert state.plan.plan == ent.PLAN_FREE
    assert state.writable_property_ids == [props[0].id, props[1].id]
    assert state.locked_property_ids == [props[2].id, props[3].id, props[4].id]
    assert state.property_count == 5


def test_the_locked_set_is_stable_across_repeated_resolution(db_session):
    """If this can vary, editing one property could silently lock another — the exact
    failure that ruled out a random or activity-based choice."""
    _owner(db_session, granted_plan=None)
    _properties(db_session, 6)
    gate = EntitlementGate(db_session)

    first = gate.state_for(OWNER_A).locked_property_ids
    for _ in range(5):
        assert gate.state_for(OWNER_A).locked_property_ids == first


def test_nothing_locks_when_the_plan_covers_everything(db_session):
    _owner(db_session, granted_plan=None)
    _properties(db_session, 2)
    state = EntitlementGate(db_session).state_for(OWNER_A)
    assert state.locked_property_ids == []
    assert state.over_limit is False


# ── Enforcement ──────────────────────────────────────────────────────────────


def test_nothing_is_refused_while_the_switch_is_off(db_session):
    """The default. Over the limit, deep over the limit, and still allowed — the answer
    is computed and reported, but not acted on."""
    assert settings.ENTITLEMENT_ENFORCED is False

    _owner(db_session, granted_plan=None)
    props = _properties(db_session, 9)
    gate = EntitlementGate(db_session)

    state = gate.state_for(OWNER_A)
    assert state.over_limit is True  # the answer is still computed
    assert state.enforced is False

    gate.require_can_add_property(OWNER_A)  # does not raise
    gate.require_property_unlocked(OWNER_A, props[-1].id)  # does not raise


def test_adding_past_the_ceiling_is_refused_with_402(db_session, enforced):
    _owner(db_session, granted_plan=None)
    _properties(db_session, 2)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        EntitlementGate(db_session).require_can_add_property(OWNER_A)

    assert raised.value.status_code == 402
    detail = raised.value.detail
    assert detail["error"] == "plan_limit_reached"
    assert detail["current_count"] == 2
    assert detail["limit"] == 2
    # The plan that covers the property they are trying to add, not the one they have.
    assert detail["required_plan"] == ent.PLAN_TIER_3_8


def test_adding_below_the_ceiling_is_allowed(db_session, enforced):
    _owner(db_session, granted_plan=None)
    _properties(db_session, 1)
    EntitlementGate(db_session).require_can_add_property(OWNER_A)  # does not raise


def test_writing_to_a_locked_property_is_refused_and_to_a_kept_one_is_not(
    db_session, enforced
):
    _owner(db_session, granted_plan=None)
    props = _properties(db_session, 4)
    gate = EntitlementGate(db_session)

    from fastapi import HTTPException

    gate.require_property_unlocked(OWNER_A, props[0].id)  # oldest — still writable
    gate.require_property_unlocked(OWNER_A, props[1].id)

    with pytest.raises(HTTPException) as raised:
        gate.require_property_unlocked(OWNER_A, props[3].id)
    assert raised.value.status_code == 402
    assert raised.value.detail["error"] == "property_locked"
    assert raised.value.detail["property_id"] == props[3].id


def test_a_record_attached_to_no_property_is_never_gated(db_session, enforced):
    _owner(db_session, granted_plan=None)
    _properties(db_session, 9)
    EntitlementGate(db_session).require_property_unlocked(OWNER_A, None)  # no raise


def test_a_grandfathered_account_is_never_refused(db_session, enforced):
    """Enforcement on, 40 properties, nothing paid — and still untouched."""
    _owner(db_session, granted_plan=ent.PLAN_TIER_16_PLUS)
    props = _properties(db_session, 40)
    gate = EntitlementGate(db_session)

    gate.require_can_add_property(OWNER_A)
    gate.require_property_unlocked(OWNER_A, props[-1].id)


# ── Feature gates: lease scans and the assistant ─────────────────────────────


def _scan(db_session, status_value, when=None):
    from app.models.document_extraction_log import DocumentExtractionLog

    db_session.add(
        DocumentExtractionLog(
            owner_id=OWNER_A,
            status=status_value,
            created_at=when or datetime.utcnow(),
        )
    )
    db_session.commit()


def test_the_free_plan_allows_three_scans_then_refuses(db_session, enforced):
    from fastapi import HTTPException

    _owner(db_session, granted_plan=None)
    gate = EntitlementGate(db_session)

    for _ in range(3):
        gate.require_lease_scan(OWNER_A)  # no raise while allowance remains
        _scan(db_session, "success")

    with pytest.raises(HTTPException) as raised:
        gate.require_lease_scan(OWNER_A)

    assert raised.value.status_code == 402
    assert raised.value.detail["error"] == "scan_limit_reached"
    assert raised.value.detail["limit"] == 3
    assert raised.value.detail["used"] == 3
    # The client needs to be able to say when it comes back.
    assert raised.value.detail["resets_at"]


def test_failed_scans_do_not_burn_the_allowance(db_session, enforced):
    """A scan that failed on an unreadable file cost the landlord nothing. Charging a
    third of a monthly allowance for it would be indefensible."""
    _owner(db_session, granted_plan=None)
    for _ in range(5):
        _scan(db_session, "error")
    for _ in range(3):
        _scan(db_session, "unsupported")

    EntitlementGate(db_session).require_lease_scan(OWNER_A)  # does not raise


def test_last_months_scans_do_not_count(db_session, enforced):
    _owner(db_session, granted_plan=None)
    for _ in range(9):
        _scan(db_session, "success", datetime.utcnow() - timedelta(days=45))

    EntitlementGate(db_session).require_lease_scan(OWNER_A)  # does not raise


def test_a_paid_plan_has_no_scan_ceiling(db_session, enforced):
    _owner(db_session, granted_plan=ent.PLAN_TIER_3_8)
    for _ in range(50):
        _scan(db_session, "success")

    EntitlementGate(db_session).require_lease_scan(OWNER_A)  # does not raise


def test_the_assistant_is_refused_on_free_and_allowed_on_paid(db_session, enforced):
    from fastapi import HTTPException

    _owner(db_session, granted_plan=None)
    with pytest.raises(HTTPException) as raised:
        EntitlementGate(db_session).require_agent(OWNER_A)
    assert raised.value.status_code == 402
    assert raised.value.detail["error"] == "agent_not_included"
    assert raised.value.detail["required_plan"] == ent.PLAN_TIER_3_8

    _owner(db_session, granted_plan=ent.PLAN_TIER_3_8)
    EntitlementGate(db_session).require_agent(OWNER_A)  # does not raise


def test_neither_feature_gate_fires_while_enforcement_is_off(db_session):
    _owner(db_session, granted_plan=None)
    for _ in range(20):
        _scan(db_session, "success")
    gate = EntitlementGate(db_session)

    gate.require_lease_scan(OWNER_A)
    gate.require_agent(OWNER_A)

"""GET /subscription, the one-time lock notice, and the per-property `locked` flag.

This endpoint is the only place a client learns what its plan permits, so the shape it
returns is a contract with two apps that ship on store review cycles. The tests lean on
that: they assert the fields exist and mean what the clients will assume, not merely that
the handler returns 200.
"""
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.models.document_extraction_log import DocumentExtractionLog
from app.models.owner import Owner
from app.models.property import Property, PropertyTypeEnum
from app.repositories.subscription_repository import SubscriptionRepository
from app.services import entitlement_service as ent
from tests.conftest import OWNER_A

FUTURE = datetime(2027, 6, 1)


@pytest.fixture
def enforced():
    original = settings.ENTITLEMENT_ENFORCED
    settings.ENTITLEMENT_ENFORCED = True
    yield
    settings.ENTITLEMENT_ENFORCED = original


def _owner(db, granted_plan=None, ack=None):
    owner = db.get(Owner, OWNER_A)
    if owner is None:
        owner = Owner(id=OWNER_A, email="a@b.c")
        db.add(owner)
    owner.granted_plan = granted_plan
    owner.lock_notice_ack_plan = ack
    db.commit()
    return owner


def _properties(db, count):
    base = datetime(2026, 1, 1, 12, 0, 0)
    made = []
    for i in range(count):
        prop = Property(
            owner_id=OWNER_A,
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


# ── The endpoint ─────────────────────────────────────────────────────────────


def test_an_account_with_nothing_reports_the_free_plan(client, db_session):
    _owner(db_session)
    body = client.get("/subscription").json()

    assert body["plan"] == ent.PLAN_FREE
    assert body["limit"] == 2
    assert body["property_count"] == 0
    assert body["locked_property_ids"] == []
    assert body["source"] is None
    assert body["monthly_lease_scans"] == 3
    assert body["agent"] is False


def test_a_subscribed_account_reports_its_billing_details(client, db_session):
    _owner(db_session)
    SubscriptionRepository(db_session).upsert(
        OWNER_A,
        plan=ent.PLAN_TIER_9_15,
        period="yearly",
        status="active",
        source="apple",
        current_period_end=FUTURE,
        price_amount=200.0,
        price_currency="USD",
    )
    body = client.get("/subscription").json()

    assert body["plan"] == ent.PLAN_TIER_9_15
    assert body["limit"] == 15
    assert body["period"] == "yearly"
    assert body["status"] == "active"
    # `source` is what tells a client where to send someone who wants to cancel.
    assert body["source"] == "apple"
    assert body["price_amount"] == 200.0
    assert body["price_currency"] == "USD"
    assert body["agent"] is True
    assert body["monthly_lease_scans"] is None  # unlimited, not zero


def test_unlimited_is_null_rather_than_a_large_number(client, db_session):
    """A client must be able to tell "no ceiling" from "a very high ceiling"."""
    _owner(db_session, granted_plan=ent.PLAN_TIER_16_PLUS)
    body = client.get("/subscription").json()
    assert body["limit"] is None
    assert body["monthly_lease_scans"] is None


def test_the_enforced_flag_reflects_the_server_switch(client, db_session, enforced):
    _owner(db_session)
    assert client.get("/subscription").json()["enforced"] is True


def test_enforced_is_false_by_default(client, db_session):
    _owner(db_session)
    assert client.get("/subscription").json()["enforced"] is False


def test_locked_ids_are_reported_even_when_enforcement_is_off(client, db_session):
    """The answer is computed either way — that is what makes it possible to see who
    would be affected before switching enforcement on."""
    _owner(db_session)
    props = _properties(db_session, 4)
    body = client.get("/subscription").json()

    assert body["property_count"] == 4
    assert body["locked_property_ids"] == [props[2].id, props[3].id]


def test_scan_usage_counts_successes_this_month(client, db_session):
    _owner(db_session)
    now = datetime.utcnow()
    for status_value in ("success", "success", "error"):
        db_session.add(
            DocumentExtractionLog(
                owner_id=OWNER_A, status=status_value, created_at=now
            )
        )
    db_session.commit()

    body = client.get("/subscription").json()
    assert body["lease_scans_used"] == 2
    assert body["monthly_lease_scans"] == 3


# ── The one-time notice ──────────────────────────────────────────────────────


def test_the_notice_is_shown_when_properties_are_locked(client, db_session):
    _owner(db_session)
    _properties(db_session, 5)
    assert client.get("/subscription").json()["show_lock_notice"] is True


def test_the_notice_is_not_shown_when_nothing_is_locked(client, db_session):
    _owner(db_session)
    _properties(db_session, 2)
    assert client.get("/subscription").json()["show_lock_notice"] is False


def test_acknowledging_stops_the_notice(client, db_session):
    _owner(db_session)
    _properties(db_session, 5)

    assert client.get("/subscription").json()["show_lock_notice"] is True
    assert client.post("/subscription/lock-notice/ack").status_code == 204
    assert client.get("/subscription").json()["show_lock_notice"] is False


def test_the_notice_returns_after_a_downgrade_to_a_different_plan(client, db_session):
    """The reason the acknowledgment stores a plan rather than a boolean. A second
    downgrade locks a different set of properties, and a 'seen it once' flag would leave
    the landlord to work that out unaided."""
    _owner(db_session)
    _properties(db_session, 12)
    repository = SubscriptionRepository(db_session)

    # On tier_3_8, four properties are over the ceiling. Shown, then acknowledged.
    repository.upsert(
        OWNER_A,
        plan=ent.PLAN_TIER_3_8,
        period="monthly",
        status="active",
        source="paddle",
        current_period_end=FUTURE,
    )
    assert client.get("/subscription").json()["show_lock_notice"] is True
    client.post("/subscription/lock-notice/ack")
    assert client.get("/subscription").json()["show_lock_notice"] is False

    # The subscription lapses entirely: now ten are locked, not four. Say so again.
    repository.upsert(
        OWNER_A,
        plan=ent.PLAN_TIER_3_8,
        period="monthly",
        status="expired",
        source="paddle",
    )
    assert client.get("/subscription").json()["show_lock_notice"] is True


def test_acknowledging_when_nothing_is_locked_is_harmless(client, db_session):
    _owner(db_session)
    _properties(db_session, 1)
    assert client.post("/subscription/lock-notice/ack").status_code == 204


# ── The per-property flag ────────────────────────────────────────────────────


def test_the_property_list_marks_which_are_locked(client, db_session):
    _owner(db_session)
    props = _properties(db_session, 4)

    listed = client.get("/properties").json()
    by_id = {p["id"]: p["locked"] for p in listed}

    assert by_id[props[0].id] is False
    assert by_id[props[1].id] is False
    assert by_id[props[2].id] is True
    assert by_id[props[3].id] is True


def test_the_detail_page_agrees_with_the_list(client, db_session):
    """Both read the same resolution, so a badge in the list and the state of the detail
    screen cannot disagree."""
    _owner(db_session)
    props = _properties(db_session, 4)

    assert client.get(f"/properties/{props[0].id}").json()["locked"] is False
    assert client.get(f"/properties/{props[3].id}").json()["locked"] is True


def test_nothing_is_marked_locked_for_a_grandfathered_account(client, db_session):
    _owner(db_session, granted_plan=ent.PLAN_TIER_16_PLUS)
    _properties(db_session, 25)
    assert all(p["locked"] is False for p in client.get("/properties").json())


def test_a_locked_property_still_reads_fully(client, db_session, enforced):
    """Locked means read-only, never hidden. `/refunds` promises publicly that data is
    never removed because of a plan change."""
    _owner(db_session)
    props = _properties(db_session, 4)

    response = client.get(f"/properties/{props[3].id}")
    assert response.status_code == 200
    assert response.json()["address"] == "4 Test St"


def test_writing_to_a_locked_property_is_refused_through_the_api(
    client, db_session, enforced
):
    _owner(db_session)
    props = _properties(db_session, 4)

    allowed = client.patch(f"/properties/{props[0].id}", json={"city": "Newville"})
    assert allowed.status_code == 200

    refused = client.patch(f"/properties/{props[3].id}", json={"city": "Newville"})
    assert refused.status_code == 402
    assert refused.json()["detail"]["error"] == "property_locked"

"""Full-stack tests for /renters."""
from datetime import date, timedelta

from freezegun import freeze_time

from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_property, make_renter, make_transaction
from app.models.transaction import TransactionTypeEnum


def _renter_payload(**kw):
    payload = {
        "first_name": "Dana",
        "last_name": "Levi",
        "phone": "0501112222",
        "email": "dana@example.com",
        "lease_years": [{"amount": 18000, "type": "contract"}],
    }
    payload.update(kw)
    return payload


def test_create_renter_without_property(client):
    resp = client.post("/renters", json=_renter_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["first_name"] == "Dana"
    assert body["lease_years"] == [{"amount": 18000, "type": "contract"}]


def test_create_renter_without_email(client):
    payload = _renter_payload()
    payload.pop("email")
    resp = client.post("/renters", json=payload)
    assert resp.status_code == 201
    assert resp.json()["email"] is None


def test_create_renter_computes_lease_end(client, db_session):
    prop = make_property(db_session)
    payload = _renter_payload(
        property_id=prop.id,
        lease_start="2025-01-01",
        lease_years=[
            {"amount": 12000, "type": "contract"},
            {"amount": 13000, "type": "option"},
        ],
    )
    resp = client.post("/renters", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    # lease_start + len(lease_years)=2 years
    assert body["lease_start"] == "2025-01-01"
    assert body["property"]["id"] == prop.id


def test_create_renter_persists_lease_intent(client):
    """The structured escalation intent round-trips so an edit re-opens with the
    same rule instead of falling back to 'custom'."""
    payload = _renter_payload(
        lease_start="2025-01-01",
        contract_term_years=2,
        option_years=1,
        base_rent=5000,
        rent_escalation_mode="percent",
        rent_escalation_value=5,
        lease_years=[
            {"amount": 5000, "type": "contract"},
            {"amount": 5250, "type": "contract"},
            {"amount": 5513, "type": "option"},
        ],
    )
    created = client.post("/renters", json=payload)
    assert created.status_code == 201
    renter_id = created.json()["id"]

    body = client.get(f"/renters/{renter_id}").json()
    assert body["contract_term_years"] == 2
    assert body["option_years"] == 1
    assert body["base_rent"] == 5000
    assert body["rent_escalation_mode"] == "percent"
    assert body["rent_escalation_value"] == 5


def test_update_renter_changes_lease_intent(client):
    created = client.post(
        "/renters",
        json=_renter_payload(rent_escalation_mode="none", base_rent=5000),
    )
    renter_id = created.json()["id"]
    resp = client.patch(
        f"/renters/{renter_id}",
        json={"rent_escalation_mode": "fixed", "rent_escalation_value": 200},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rent_escalation_mode"] == "fixed"
    assert body["rent_escalation_value"] == 200


def test_create_renter_foreign_property_forbidden(client_factory, db_session):
    prop = make_property(db_session, owner_id=OWNER_B)
    client_a = client_factory(OWNER_A)
    resp = client_a.post("/renters", json=_renter_payload(property_id=prop.id))
    assert resp.status_code == 403


def test_create_renter_invalid_payment_day(client):
    resp = client.post("/renters", json=_renter_payload(payment_day_of_month=45))
    assert resp.status_code == 422


def test_list_renters_owner_scoped(client_factory, db_session):
    make_renter(db_session, owner_id=OWNER_A, first_name="Amy")
    make_renter(db_session, owner_id=OWNER_B, first_name="Ben")
    client_a = client_factory(OWNER_A)
    names = [r["first_name"] for r in client_a.get("/renters").json()]
    assert names == ["Amy"]


def test_get_renter(client, db_session):
    renter = make_renter(db_session)
    body = client.get(f"/renters/{renter.id}").json()
    assert body["id"] == renter.id


def test_get_renter_not_found(client):
    assert client.get("/renters/999").status_code == 404


def test_get_renter_other_owner_forbidden(client_factory, db_session):
    renter = make_renter(db_session, owner_id=OWNER_B)
    client_a = client_factory(OWNER_A)
    assert client_a.get(f"/renters/{renter.id}").status_code == 403


def test_update_renter_move_property(client, db_session):
    prop1 = make_property(db_session, address="P1")
    prop2 = make_property(db_session, address="P2")
    renter = make_renter(db_session, property_id=prop1.id)
    resp = client.patch(f"/renters/{renter.id}", json={"property_id": prop2.id})
    assert resp.status_code == 200
    assert resp.json()["property_id"] == prop2.id


def test_update_renter_to_foreign_property_forbidden(client_factory, db_session):
    foreign = make_property(db_session, owner_id=OWNER_B)
    renter = make_renter(db_session, owner_id=OWNER_A)
    client_a = client_factory(OWNER_A)
    resp = client_a.patch(f"/renters/{renter.id}", json={"property_id": foreign.id})
    assert resp.status_code == 403


def test_delete_renter(client, db_session):
    renter = make_renter(db_session)
    assert client.delete(f"/renters/{renter.id}").status_code == 204
    assert client.get(f"/renters/{renter.id}").status_code == 404


def test_delete_renter_not_found(client):
    assert client.delete("/renters/999").status_code == 404


@freeze_time("2026-06-15")
def test_overdue_renters(client, db_session):
    today = date(2026, 6, 15)
    prop = make_property(db_session, property_owner="Alice")
    # Overdue: active lease, payment day already passed, no revenue this month.
    overdue = make_renter(
        db_session,
        property_id=prop.id,
        first_name="Late",
        lease_start=today - timedelta(days=100),
        lease_end=today + timedelta(days=200),
        payment_day_of_month=1,
        lease_years=[{"amount": 9000, "type": "contract"}],
    )
    # Paid: same setup but has a revenue transaction for this month.
    paid = make_renter(
        db_session,
        property_id=prop.id,
        first_name="Paid",
        lease_start=today - timedelta(days=100),
        lease_end=today + timedelta(days=200),
        payment_day_of_month=1,
    )
    make_transaction(
        db_session,
        type=TransactionTypeEnum.REVENUE,
        property_id=prop.id,
        renter_id=paid.id,
        month_for=date(2026, 6, 1),
        date_of_payment=today,
    )

    body = client.get("/renters/overdue").json()
    ids = {r["renter_id"] for r in body}
    assert overdue.id in ids
    assert paid.id not in ids
    row = next(r for r in body if r["renter_id"] == overdue.id)
    assert row["days_overdue"] == 14  # 15th minus payment day 1
    assert row["monthly_amount"] == 9000


@freeze_time("2026-06-15")
def test_overdue_renters_property_owner_filter(client, db_session):
    today = date(2026, 6, 15)
    prop_alice = make_property(db_session, property_owner="Alice")
    prop_bob = make_property(db_session, property_owner="Bob")
    for prop in (prop_alice, prop_bob):
        make_renter(
            db_session,
            property_id=prop.id,
            lease_start=today - timedelta(days=50),
            lease_end=today + timedelta(days=200),
            payment_day_of_month=1,
        )
    body = client.get("/renters/overdue", params={"property_owner": "Alice"}).json()
    assert len(body) == 1
    assert body[0]["property_owner"] == "Alice"


@freeze_time("2026-06-15")
def test_expiring_renters(client, db_session):
    today = date(2026, 6, 15)
    prop = make_property(db_session)
    soon = make_renter(
        db_session,
        property_id=prop.id,
        first_name="Soon",
        lease_start=today - timedelta(days=300),
        lease_end=today + timedelta(days=30),
    )
    make_renter(
        db_session,
        property_id=prop.id,
        first_name="Later",
        lease_start=today - timedelta(days=10),
        lease_end=today + timedelta(days=300),
    )
    body = client.get("/renters/expiring", params={"days_until": 90}).json()
    assert [r["renter_id"] for r in body] == [soon.id]
    assert body[0]["days_until_expiry"] == 30


# ── Ending a lease early ─────────────────────────────────────────────────────────


def _running_lease(db_session, prop, **kw):
    """A lease that started 100 days ago and runs another 200 — overdue and, at a
    90-day horizon, not yet expiring."""
    today = date(2026, 6, 15)
    defaults = dict(
        property_id=prop.id,
        lease_start=today - timedelta(days=100),
        lease_end=today + timedelta(days=200),
        payment_day_of_month=1,
        lease_years=[{"amount": 9000, "type": "contract"}],
    )
    defaults.update(kw)
    return make_renter(db_session, **defaults)


@freeze_time("2026-06-15")
def test_terminate_lease_stops_overdue_chasing(client, db_session):
    prop = make_property(db_session)
    renter = _running_lease(db_session, prop)
    assert renter.id in {r["renter_id"] for r in client.get("/renters/overdue").json()}

    resp = client.post(
        f"/renters/{renter.id}/terminate",
        json={"terminated_on": "2026-06-10", "reason": "Moved out"},
    )
    assert resp.status_code == 200
    assert resp.json()["terminated_on"] == "2026-06-10"
    assert resp.json()["termination_reason"] == "Moved out"

    assert renter.id not in {r["renter_id"] for r in client.get("/renters/overdue").json()}


@freeze_time("2026-06-15")
def test_terminate_lease_preserves_the_signed_schedule(client, db_session):
    """The whole point: closing a lease must not rewrite what it was priced from."""
    prop = make_property(db_session)
    renter = _running_lease(
        db_session,
        prop,
        lease_years=[{"amount": 9000, "type": "contract"}, {"amount": 9500, "type": "contract"}],
        cpi_base_index=101.5,
    )

    body = client.post(
        f"/renters/{renter.id}/terminate", json={"terminated_on": "2026-06-10"}
    ).json()

    assert body["lease_years"] == [
        {"amount": 9000, "type": "contract"},
        {"amount": 9500, "type": "contract"},
    ]
    assert body["cpi_base_index"] == 101.5
    assert body["lease_start"] == (date(2026, 6, 15) - timedelta(days=100)).isoformat()


@freeze_time("2026-06-15")
def test_terminated_lease_is_not_reported_as_expiring(client, db_session):
    prop = make_property(db_session)
    today = date(2026, 6, 15)
    renter = _running_lease(db_session, prop, lease_end=today + timedelta(days=30))
    assert renter.id in {r["renter_id"] for r in client.get("/renters/expiring").json()}

    client.post(f"/renters/{renter.id}/terminate", json={"terminated_on": "2026-06-10"})
    assert renter.id not in {r["renter_id"] for r in client.get("/renters/expiring").json()}


@freeze_time("2026-06-15")
def test_undo_termination_restores_the_renter(client, db_session):
    prop = make_property(db_session)
    renter = _running_lease(db_session, prop)
    client.post(f"/renters/{renter.id}/terminate", json={"terminated_on": "2026-06-10"})

    resp = client.delete(f"/renters/{renter.id}/terminate")
    assert resp.status_code == 200
    assert resp.json()["terminated_on"] is None
    assert resp.json()["termination_reason"] is None
    assert renter.id in {r["renter_id"] for r in client.get("/renters/overdue").json()}


@freeze_time("2026-06-15")
def test_terminate_rejects_a_date_outside_the_lease(client, db_session):
    prop = make_property(db_session)
    renter = _running_lease(db_session, prop)

    before = client.post(
        f"/renters/{renter.id}/terminate", json={"terminated_on": "2020-01-01"}
    )
    assert before.status_code == 400

    after = client.post(
        f"/renters/{renter.id}/terminate", json={"terminated_on": "2030-01-01"}
    )
    assert after.status_code == 400


@freeze_time("2026-06-15")
def test_terminate_renter_of_another_owner_is_denied(client, db_session):
    prop = make_property(db_session, owner_id=OWNER_B)
    renter = _running_lease(db_session, prop, owner_id=OWNER_B)
    resp = client.post(
        f"/renters/{renter.id}/terminate", json={"terminated_on": "2026-06-10"}
    )
    assert resp.status_code == 403


def test_terminate_renter_not_found(client):
    resp = client.post("/renters/999/terminate", json={"terminated_on": "2026-06-10"})
    assert resp.status_code == 404


@freeze_time("2026-06-15")
def test_property_renters_hides_then_reveals_an_ended_lease(client, db_session):
    """The transaction form needs the ended tenant back — a rent payment routinely
    lands after the tenancy finishes, and editing its transaction must still show
    the renter it is attached to."""
    prop = make_property(db_session)
    renter = _running_lease(db_session, prop)
    client.post(f"/renters/{renter.id}/terminate", json={"terminated_on": "2026-06-10"})

    default = client.get(f"/properties/{prop.id}/renters").json()
    assert renter.id not in {r["id"] for r in default}

    included = client.get(
        f"/properties/{prop.id}/renters", params={"include_ended": True}
    ).json()
    row = next(r for r in included if r["id"] == renter.id)
    assert row["is_ended"] is True

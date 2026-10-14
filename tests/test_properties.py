"""Full-stack tests for /properties."""
from datetime import date, timedelta

from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_property, make_renter

VALID_PAYLOAD = {
    "address": "10 Rothschild Blvd",
    "city": "Tel Aviv",
    "zip_code": "61000",
    "type": "apartment",
    "sq_ft": 95,
    "purchase_price": 2_500_000,
    "parking_numbers": ["A12", "A13"],
}


def test_create_property(client):
    resp = client.post("/properties", json=VALID_PAYLOAD)
    assert resp.status_code == 201
    body = resp.json()
    assert body["address"] == "10 Rothschild Blvd"
    assert body["owner_id"] == OWNER_A
    assert body["parking_numbers"] == ["A12", "A13"]  # JSON round-trips through Text
    assert body["hasRenters"] is False


def test_create_property_validation_error(client):
    resp = client.post("/properties", json={"address": "no city"})
    assert resp.status_code == 422


def test_list_properties_only_returns_own(client_factory, db_session):
    make_property(db_session, owner_id=OWNER_A, address="A-owned")
    make_property(db_session, owner_id=OWNER_B, address="B-owned")

    client_a = client_factory(OWNER_A)
    body = client_a.get("/properties").json()
    assert [p["address"] for p in body] == ["A-owned"]

    client_b = client_factory(OWNER_B)
    body = client_b.get("/properties").json()
    assert [p["address"] for p in body] == ["B-owned"]


def test_get_property_has_renters_flag(client, db_session):
    prop = make_property(db_session)
    make_renter(db_session, property_id=prop.id)
    body = client.get(f"/properties/{prop.id}").json()
    assert body["hasRenters"] is True
    assert len(body["renters"]) == 1


def test_has_renters_ignores_past_tenants(client, db_session):
    """Occupied means a tenancy that has not finished.

    Past tenants stay on `renters` — the apps list them under "previous tenants" — so the
    flag has to look at the dates rather than the length, or a flat that was let once in
    2020 reads as occupied for ever.
    """
    prop = make_property(db_session)
    make_renter(
        db_session, property_id=prop.id, lease_start=date(2020, 1, 1), lease_end=date(2021, 1, 1)
    )

    body = client.get(f"/properties/{prop.id}").json()
    assert body["hasRenters"] is False
    assert len(body["renters"]) == 1  # still returned, for the "previous tenants" list

    today = date.today()
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=today - timedelta(days=30),
        lease_end=today + timedelta(days=335),
    )
    assert client.get(f"/properties/{prop.id}").json()["hasRenters"] is True


def test_has_renters_ignores_terminated_lease(client, db_session):
    """An early termination ends the tenancy before the signed schedule does."""
    prop = make_property(db_session)
    today = date.today()
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=today - timedelta(days=200),
        lease_end=today + timedelta(days=165),
        terminated_on=today - timedelta(days=1),
    )

    assert client.get(f"/properties/{prop.id}").json()["hasRenters"] is False


def test_has_renters_counts_an_upcoming_lease(client, db_session):
    """A lease signed to start next month already holds the property."""
    prop = make_property(db_session)
    today = date.today()
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=today + timedelta(days=30),
        lease_end=today + timedelta(days=395),
    )

    assert client.get(f"/properties/{prop.id}").json()["hasRenters"] is True


def test_get_property_not_found(client):
    assert client.get("/properties/999").status_code == 404


def test_get_property_other_owner_is_404(client_factory, db_session):
    prop = make_property(db_session, owner_id=OWNER_B)
    client_a = client_factory(OWNER_A)
    assert client_a.get(f"/properties/{prop.id}").status_code == 404


def test_update_property(client, db_session):
    prop = make_property(db_session, address="old")
    resp = client.patch(f"/properties/{prop.id}", json={"address": "new", "floor": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["address"] == "new"
    assert body["floor"] == 3


def test_update_property_not_found(client):
    assert client.patch("/properties/999", json={"address": "x"}).status_code == 404


def test_delete_property_unassigns_renters(client, db_session):
    prop = make_property(db_session)
    renter = make_renter(db_session, property_id=prop.id)

    resp = client.delete(f"/properties/{prop.id}")
    assert resp.status_code == 204

    db_session.expire_all()
    assert client.get(f"/properties/{prop.id}").status_code == 404
    # Renter survives but is unassigned (property_id -> NULL).
    db_session.refresh(renter)
    assert renter.property_id is None


def test_delete_property_not_found(client):
    assert client.delete("/properties/999").status_code == 404


def test_property_renters_active_only_with_monthly_rent(client, db_session):
    prop = make_property(db_session)
    today = date.today()
    make_renter(
        db_session,
        property_id=prop.id,
        first_name="Active",
        lease_start=today - timedelta(days=10),
        lease_end=today + timedelta(days=365),
        lease_years=[{"amount": 24000.0, "type": "contract"}],
    )
    make_renter(
        db_session,
        property_id=prop.id,
        first_name="Expired",
        lease_start=today - timedelta(days=800),
        lease_end=today - timedelta(days=400),
    )

    body = client.get(f"/properties/{prop.id}/renters").json()
    assert len(body) == 1
    assert body[0]["first_name"] == "Active"
    # lease_years amount is the MONTHLY rent — returned as-is, not divided by 12.
    assert body[0]["monthly_rent"] == 24000.0


def test_property_renters_not_found(client):
    assert client.get("/properties/999/renters").status_code == 404


def test_property_files_bulk_create_list_and_delete(client, db_session):
    prop = make_property(db_session)
    files = [
        {"url": "https://x/a.pdf", "label": "Contract"},
        {"url": "https://x/b.pdf", "label": "ID"},
    ]
    resp = client.post(f"/properties/{prop.id}/files/bulk", json=files)
    assert resp.status_code == 201
    created = resp.json()
    assert len(created) == 2

    listed = client.get(f"/properties/{prop.id}/files").json()
    assert {f["label"] for f in listed} == {"Contract", "ID"}

    file_id = created[0]["id"]
    assert client.delete(f"/properties/{prop.id}/files/{file_id}").status_code == 204
    assert len(client.get(f"/properties/{prop.id}/files").json()) == 1


def test_property_files_delete_missing_file(client, db_session):
    prop = make_property(db_session)
    assert client.delete(f"/properties/{prop.id}/files/999").status_code == 404


def test_property_files_other_owner_blocked(client_factory, db_session):
    prop = make_property(db_session, owner_id=OWNER_B)
    client_a = client_factory(OWNER_A)
    assert client_a.get(f"/properties/{prop.id}/files").status_code == 404


# --- Purchase date and value history -------------------------------------------------


def test_purchase_price_and_date_round_trip_and_clear(client):
    payload = {**VALID_PAYLOAD, "purchase_price": 1_800_000, "purchase_date": "2019-06-01"}
    body = client.post("/properties", json=payload).json()
    assert body["purchase_price"] == 1_800_000
    assert body["purchase_date"] == "2019-06-01"

    cleared = client.patch(
        f"/properties/{body['id']}", json={"purchase_price": None, "purchase_date": None}
    ).json()
    assert cleared["purchase_price"] is None
    assert cleared["purchase_date"] is None


def test_purchase_price_zero_is_stored_as_none(client):
    """0 is what an old mobile build sent for a blank field — not a price."""
    body = client.post("/properties", json={**VALID_PAYLOAD, "purchase_price": 0}).json()
    assert body["purchase_price"] is None


def test_purchase_price_negative_rejected(client):
    resp = client.post("/properties", json={**VALID_PAYLOAD, "purchase_price": -5})
    assert resp.status_code == 422


def test_property_without_valuations_has_no_current_value(client):
    body = client.post("/properties", json=VALID_PAYLOAD).json()
    assert body["current_value"] is None
    assert body["current_value_date"] is None


def test_create_with_valuation_sets_current_value(client):
    payload = {**VALID_PAYLOAD, "valuation": {"value": 3_000_000, "valued_on": "2026-01-10"}}
    body = client.post("/properties", json=payload).json()
    assert body["current_value"] == 3_000_000
    assert body["current_value_date"] == "2026-01-10"
    assert "valuation" not in body


def test_update_with_valuation_appends_to_history(client):
    created = client.post(
        "/properties",
        json={**VALID_PAYLOAD, "valuation": {"value": 3_000_000, "valued_on": "2025-01-10"}},
    ).json()
    updated = client.patch(
        f"/properties/{created['id']}",
        json={"valuation": {"value": 3_200_000, "valued_on": "2026-01-10"}},
    ).json()
    assert updated["current_value"] == 3_200_000

    history = client.get(f"/properties/{created['id']}/valuations").json()
    assert [v["value"] for v in history] == [3_200_000, 3_000_000]


def test_current_value_is_latest_by_date_not_by_entry(client, db_session):
    """Backfilling an older estimate must not displace the newer one."""
    prop = make_property(db_session)
    client.post(f"/properties/{prop.id}/valuations", json={"value": 900, "valued_on": "2026-03-01"})
    client.post(f"/properties/{prop.id}/valuations", json={"value": 700, "valued_on": "2024-03-01"})

    body = client.get(f"/properties/{prop.id}").json()
    assert body["current_value"] == 900
    assert body["current_value_date"] == "2026-03-01"
    listed = next(p for p in client.get("/properties").json() if p["id"] == prop.id)
    assert listed["current_value"] == 900

    history = client.get(f"/properties/{prop.id}/valuations").json()
    assert [v["valued_on"] for v in history] == ["2026-03-01", "2024-03-01"]


def test_valuation_validation(client, db_session):
    prop = make_property(db_session)
    url = f"/properties/{prop.id}/valuations"
    assert client.post(url, json={"value": 0, "valued_on": "2026-01-01"}).status_code == 422
    assert client.post(url, json={"value": 100}).status_code == 422
    future = (date.today() + timedelta(days=3)).isoformat()
    assert client.post(url, json={"value": 100, "valued_on": future}).status_code == 400


def test_delete_valuation_falls_back_to_previous(client, db_session):
    prop = make_property(db_session)
    url = f"/properties/{prop.id}/valuations"
    client.post(url, json={"value": 700, "valued_on": "2024-03-01"})
    newest = client.post(url, json={"value": 900, "valued_on": "2026-03-01"}).json()

    assert client.delete(f"{url}/{newest['id']}").status_code == 204
    assert client.get(f"/properties/{prop.id}").json()["current_value"] == 700
    assert client.delete(f"{url}/{newest['id']}").status_code == 404


def test_valuations_are_owner_scoped(client_factory, db_session):
    prop = make_property(db_session, owner_id=OWNER_A)
    client_a = client_factory(OWNER_A)
    entry = client_a.post(
        f"/properties/{prop.id}/valuations", json={"value": 500, "valued_on": "2026-01-01"}
    ).json()

    client_b = client_factory(OWNER_B)
    url = f"/properties/{prop.id}/valuations"
    assert client_b.get(url).status_code == 404
    assert client_b.post(url, json={"value": 1, "valued_on": "2026-01-01"}).status_code == 404
    assert client_b.delete(f"{url}/{entry['id']}").status_code == 404


def test_deleting_property_removes_its_valuations(client, db_session):
    from app.models.property_valuation import PropertyValuation

    prop = make_property(db_session)
    client.post(f"/properties/{prop.id}/valuations", json={"value": 500, "valued_on": "2026-01-01"})
    assert client.delete(f"/properties/{prop.id}").status_code == 204
    assert db_session.query(PropertyValuation).count() == 0

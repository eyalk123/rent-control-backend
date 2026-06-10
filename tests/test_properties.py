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
    assert body[0]["monthly_rent"] == 2000.0  # 24000 / 12


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

"""Full-stack tests for /suppliers."""
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_expense_category, make_supplier


def test_create_supplier(client, db_session):
    cat = make_expense_category(db_session)
    payload = {"name": "Bright Electric", "category_ids": [cat.id], "phone": "03-1234"}
    resp = client.post("/suppliers", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Bright Electric"
    assert body["category_ids"] == [cat.id]
    assert body["is_active"] is True


def test_create_supplier_requires_category(client):
    resp = client.post("/suppliers", json={"name": "X", "category_ids": []})
    assert resp.status_code == 422


def test_create_supplier_blank_name_rejected(client, db_session):
    cat = make_expense_category(db_session)
    resp = client.post("/suppliers", json={"name": "   ", "category_ids": [cat.id]})
    assert resp.status_code == 422


def test_create_supplier_unknown_category(client):
    resp = client.post("/suppliers", json={"name": "X", "category_ids": [999]})
    assert resp.status_code == 400


def test_create_supplier_foreign_category_rejected(client_factory, db_session):
    foreign_cat = make_expense_category(db_session, owner_id=OWNER_B, name="B-cat")
    client_a = client_factory(OWNER_A)
    resp = client_a.post(
        "/suppliers", json={"name": "X", "category_ids": [foreign_cat.id]}
    )
    assert resp.status_code == 400


def test_create_supplier_with_predefined_category(client_factory, db_session):
    predefined = make_expense_category(db_session, owner_id=None, key="utilities")
    client_a = client_factory(OWNER_A)
    resp = client_a.post(
        "/suppliers", json={"name": "X", "category_ids": [predefined.id]}
    )
    assert resp.status_code == 201


def test_get_supplier(client, db_session):
    cat = make_expense_category(db_session)
    supplier = make_supplier(db_session, categories=[cat])
    body = client.get(f"/suppliers/{supplier.id}").json()
    assert body["id"] == supplier.id


def test_get_supplier_not_found(client):
    assert client.get("/suppliers/999").status_code == 404


def test_get_supplier_other_owner(client_factory, db_session):
    supplier = make_supplier(db_session, owner_id=OWNER_B)
    client_a = client_factory(OWNER_A)
    assert client_a.get(f"/suppliers/{supplier.id}").status_code == 404


def test_list_suppliers_excludes_inactive_by_default(client, db_session):
    cat = make_expense_category(db_session)
    make_supplier(db_session, name="Active", categories=[cat])
    make_supplier(db_session, name="Inactive", categories=[cat], is_active=False)

    active_only = client.get("/suppliers").json()
    assert [s["name"] for s in active_only] == ["Active"]

    with_inactive = client.get("/suppliers", params={"include_inactive": True}).json()
    assert {s["name"] for s in with_inactive} == {"Active", "Inactive"}


def test_list_suppliers_filter_by_category(client, db_session):
    cat1 = make_expense_category(db_session, name="C1")
    cat2 = make_expense_category(db_session, name="C2")
    make_supplier(db_session, name="S1", categories=[cat1])
    make_supplier(db_session, name="S2", categories=[cat2])
    body = client.get("/suppliers", params={"category_id": cat1.id}).json()
    assert [s["name"] for s in body] == ["S1"]


def test_list_suppliers_search(client, db_session):
    cat = make_expense_category(db_session)
    make_supplier(db_session, name="Plumber Pro", categories=[cat])
    make_supplier(db_session, name="Electric Co", categories=[cat])
    body = client.get("/suppliers", params={"q": "plumb"}).json()
    assert [s["name"] for s in body] == ["Plumber Pro"]


def test_update_supplier(client, db_session):
    cat1 = make_expense_category(db_session, name="C1")
    cat2 = make_expense_category(db_session, name="C2")
    supplier = make_supplier(db_session, categories=[cat1])
    resp = client.patch(
        f"/suppliers/{supplier.id}",
        json={"name": "Renamed", "category_ids": [cat2.id], "is_active": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["category_ids"] == [cat2.id]
    assert body["is_active"] is False


def test_update_supplier_not_found(client):
    assert client.patch("/suppliers/999", json={"name": "x"}).status_code == 404

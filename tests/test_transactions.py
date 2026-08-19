"""Full-stack tests for /transactions."""
from datetime import date

from freezegun import freeze_time

from tests.conftest import OWNER_A, OWNER_B
from tests.factories import (
    make_expense_category,
    make_property,
    make_renter,
    make_supplier,
    make_transaction,
)
from app.models.transaction import TransactionTypeEnum


# --- revenue creation -------------------------------------------------------

def test_create_revenue(client, db_session):
    prop = make_property(db_session, address="5 Herzl", city="Haifa")
    payload = {
        "property_id": prop.id,
        "amount": 6000,
        "month_for": "2026-05-01",
        "date_of_payment": "2026-05-03",
        "payment_method": "bank_transfer",
    }
    resp = client.post("/transactions/revenue", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["type"] == "revenue"
    assert body["amount"] == "6000.00"
    assert body["currencyCode"] == "ILS"  # property has no currency -> default
    assert body["property_name"] == "5 Herzl, Haifa"  # denormalized snapshot


def test_create_revenue_uses_property_currency(client, db_session):
    prop = make_property(db_session, currency_code="USD")
    payload = {"property_id": prop.id, "amount": 100, "month_for": "2026-05-01"}
    body = client.post("/transactions/revenue", json=payload).json()
    assert body["currencyCode"] == "USD"


def test_create_revenue_property_not_found(client):
    payload = {"property_id": 999, "amount": 100, "month_for": "2026-05-01"}
    assert client.post("/transactions/revenue", json=payload).status_code == 404


def test_create_revenue_property_other_owner(client_factory, db_session):
    prop = make_property(db_session, owner_id=OWNER_B)
    client_a = client_factory(OWNER_A)
    payload = {"property_id": prop.id, "amount": 100, "month_for": "2026-05-01"}
    assert client_a.post("/transactions/revenue", json=payload).status_code == 404


def test_create_revenue_renter_must_belong_to_property(client, db_session):
    prop = make_property(db_session)
    other_prop = make_property(db_session)
    renter = make_renter(db_session, property_id=other_prop.id)
    payload = {
        "property_id": prop.id,
        "renter_id": renter.id,
        "amount": 100,
        "month_for": "2026-05-01",
    }
    assert client.post("/transactions/revenue", json=payload).status_code == 400


def test_create_revenue_amount_must_be_positive(client, db_session):
    prop = make_property(db_session)
    payload = {"property_id": prop.id, "amount": 0, "month_for": "2026-05-01"}
    assert client.post("/transactions/revenue", json=payload).status_code == 422


# --- expense creation -------------------------------------------------------

def test_create_expense(client, db_session):
    prop = make_property(db_session)
    cat = make_expense_category(db_session, name="Plumbing")
    payload = {
        "property_id": prop.id,
        "amount": 350,
        "date_of_payment": "2026-04-10",
        "payment_method": "cash",
        "category_ids": [cat.id],
    }
    resp = client.post("/transactions/expense", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["type"] == "expense"
    assert body["category_ids"] == [cat.id]
    assert body["category_name"] == "Plumbing"


def test_create_expense_requires_category(client, db_session):
    prop = make_property(db_session)
    payload = {
        "property_id": prop.id,
        "amount": 100,
        "date_of_payment": "2026-04-10",
        "payment_method": "cash",
        "category_ids": [],
    }
    assert client.post("/transactions/expense", json=payload).status_code == 422


def test_create_expense_unknown_category(client, db_session):
    prop = make_property(db_session)
    payload = {
        "property_id": prop.id,
        "amount": 100,
        "date_of_payment": "2026-04-10",
        "payment_method": "cash",
        "category_ids": [999],
    }
    assert client.post("/transactions/expense", json=payload).status_code == 400


def test_create_expense_with_valid_supplier(client, db_session):
    prop = make_property(db_session)
    cat = make_expense_category(db_session)
    supplier = make_supplier(db_session, categories=[cat])
    payload = {
        "property_id": prop.id,
        "amount": 500,
        "date_of_payment": "2026-04-10",
        "payment_method": "bit",
        "category_ids": [cat.id],
        "supplier_id": supplier.id,
    }
    resp = client.post("/transactions/expense", json=payload)
    assert resp.status_code == 201
    assert resp.json()["supplier_name"] == supplier.name


def test_create_expense_inactive_supplier_rejected(client, db_session):
    prop = make_property(db_session)
    cat = make_expense_category(db_session)
    supplier = make_supplier(db_session, categories=[cat], is_active=False)
    payload = {
        "property_id": prop.id,
        "amount": 500,
        "date_of_payment": "2026-04-10",
        "payment_method": "bit",
        "category_ids": [cat.id],
        "supplier_id": supplier.id,
    }
    assert client.post("/transactions/expense", json=payload).status_code == 400


def test_create_expense_supplier_category_mismatch(client, db_session):
    prop = make_property(db_session)
    cat_used = make_expense_category(db_session, name="Used")
    cat_other = make_expense_category(db_session, name="Other")
    supplier = make_supplier(db_session, categories=[cat_other])
    payload = {
        "property_id": prop.id,
        "amount": 500,
        "date_of_payment": "2026-04-10",
        "payment_method": "bit",
        "category_ids": [cat_used.id],
        "supplier_id": supplier.id,
    }
    assert client.post("/transactions/expense", json=payload).status_code == 400


# --- get / list -------------------------------------------------------------

def test_get_transaction_not_found(client):
    assert client.get("/transactions/999").status_code == 404


def test_get_transaction_other_owner(client_factory, db_session):
    prop = make_property(db_session, owner_id=OWNER_B)
    txn = make_transaction(db_session, owner_id=OWNER_B, property_id=prop.id)
    client_a = client_factory(OWNER_A)
    assert client_a.get(f"/transactions/{txn.id}").status_code == 404


def test_list_transactions_owner_scoped(client_factory, db_session):
    pa = make_property(db_session, owner_id=OWNER_A)
    pb = make_property(db_session, owner_id=OWNER_B)
    make_transaction(db_session, owner_id=OWNER_A, property_id=pa.id, amount=111)
    make_transaction(db_session, owner_id=OWNER_B, property_id=pb.id, amount=222)
    client_a = client_factory(OWNER_A)
    body = client_a.get("/transactions").json()
    assert len(body) == 1
    assert body[0]["amount"] == "111.00"


def test_list_transactions_filter_by_type(client, db_session):
    prop = make_property(db_session)
    cat = make_expense_category(db_session)
    make_transaction(db_session, type=TransactionTypeEnum.REVENUE, property_id=prop.id)
    make_transaction(
        db_session,
        type=TransactionTypeEnum.EXPENSE,
        property_id=prop.id,
        categories=[cat],
    )
    body = client.get("/transactions", params={"type": "expense"}).json()
    assert len(body) == 1
    assert body[0]["type"] == "expense"


def test_list_transactions_filter_by_property(client, db_session):
    p1 = make_property(db_session)
    p2 = make_property(db_session)
    make_transaction(db_session, property_id=p1.id)
    make_transaction(db_session, property_id=p2.id)
    body = client.get("/transactions", params={"property_id": p1.id}).json()
    assert len(body) == 1
    assert body[0]["property_id"] == p1.id


def test_list_transactions_search_by_notes(client, db_session):
    prop = make_property(db_session)
    make_transaction(db_session, property_id=prop.id, notes="electric bill")
    make_transaction(db_session, property_id=prop.id, notes="water bill")
    body = client.get("/transactions", params={"q": "electric"}).json()
    assert len(body) == 1
    assert body[0]["notes"] == "electric bill"


def test_list_transactions_date_range(client, db_session):
    prop = make_property(db_session)
    make_transaction(db_session, property_id=prop.id, date_of_payment=date(2026, 1, 5))
    make_transaction(db_session, property_id=prop.id, date_of_payment=date(2026, 6, 5))
    body = client.get(
        "/transactions",
        params={"from_date": "2026-05-01", "to_date": "2026-12-31"},
    ).json()
    assert len(body) == 1
    assert body[0]["date_of_payment"] == "2026-06-05"


def test_list_transactions_pagination(client, db_session):
    prop = make_property(db_session)
    for i in range(3):
        make_transaction(db_session, property_id=prop.id, date_of_payment=date(2026, 6, i + 1))
    body = client.get("/transactions", params={"limit": 2, "offset": 0}).json()
    assert len(body) == 2


# --- update / delete --------------------------------------------------------

def test_update_revenue(client, db_session):
    prop = make_property(db_session)
    txn = make_transaction(
        db_session, type=TransactionTypeEnum.REVENUE, property_id=prop.id, amount=100
    )
    resp = client.patch(
        f"/transactions/revenue/{txn.id}", json={"amount": 250, "notes": "adjusted"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["amount"] == "250.00"
    assert body["notes"] == "adjusted"


def test_update_revenue_not_found(client):
    assert client.patch("/transactions/revenue/999", json={"amount": 1}).status_code == 404


def test_update_expense_reassign_categories(client, db_session):
    prop = make_property(db_session)
    cat1 = make_expense_category(db_session, name="Cat1")
    cat2 = make_expense_category(db_session, name="Cat2")
    txn = make_transaction(
        db_session,
        type=TransactionTypeEnum.EXPENSE,
        property_id=prop.id,
        categories=[cat1],
    )
    resp = client.patch(
        f"/transactions/expense/{txn.id}", json={"category_ids": [cat2.id]}
    )
    assert resp.status_code == 200
    assert resp.json()["category_ids"] == [cat2.id]


def test_delete_transaction(client, db_session):
    prop = make_property(db_session)
    txn = make_transaction(db_session, property_id=prop.id)
    assert client.delete(f"/transactions/{txn.id}").status_code == 204
    assert client.get(f"/transactions/{txn.id}").status_code == 404


def test_delete_transaction_not_found(client):
    assert client.delete("/transactions/999").status_code == 404


# --- summary (exercises the SQLite extract shim) ----------------------------

@freeze_time("2026-06-15")
def test_summary_six_buckets(client, db_session):
    prop = make_property(db_session)
    cat = make_expense_category(db_session)
    # Revenue in current month, expense two months ago.
    make_transaction(
        db_session,
        type=TransactionTypeEnum.REVENUE,
        property_id=prop.id,
        amount=5000,
        date_of_payment=date(2026, 6, 10),
    )
    make_transaction(
        db_session,
        type=TransactionTypeEnum.EXPENSE,
        property_id=prop.id,
        amount=1200,
        date_of_payment=date(2026, 4, 10),
        categories=[cat],
    )
    body = client.get("/transactions/summary").json()
    buckets = body["six_month_buckets"]
    assert len(buckets) == 6
    assert buckets[-1]["key"] == "2026-06"
    assert buckets[0]["key"] == "2026-01"

    june = buckets[-1]
    assert june["revenue"] == 5000.0
    assert june["profit"] == 5000.0
    april = next(b for b in buckets if b["key"] == "2026-04")
    assert april["expenses"] == 1200.0
    assert april["profit"] == -1200.0


@freeze_time("2026-06-15")
def test_summary_ytd_by_owner(client, db_session):
    jane = make_property(db_session, property_owner="Jane Cooper")
    dana = make_property(db_session, property_owner="Dana Levi")
    unowned = make_property(db_session, property_owner=None)
    cat = make_expense_category(db_session)

    # Jane: 9000 in, 1000 out -> 8000. Dana: 2000 in -> 2000. Unowned: 500 out -> -500.
    make_transaction(db_session, type=TransactionTypeEnum.REVENUE, property_id=jane.id,
                     amount=4000, date_of_payment=date(2026, 1, 5))
    make_transaction(db_session, type=TransactionTypeEnum.REVENUE, property_id=jane.id,
                     amount=5000, date_of_payment=date(2026, 6, 5))
    make_transaction(db_session, type=TransactionTypeEnum.EXPENSE, property_id=jane.id,
                     amount=1000, date_of_payment=date(2026, 3, 5), categories=[cat])
    make_transaction(db_session, type=TransactionTypeEnum.REVENUE, property_id=dana.id,
                     amount=2000, date_of_payment=date(2026, 2, 5))
    make_transaction(db_session, type=TransactionTypeEnum.EXPENSE, property_id=unowned.id,
                     amount=500, date_of_payment=date(2026, 4, 5), categories=[cat])
    # Last year: outside the window, must not count.
    make_transaction(db_session, type=TransactionTypeEnum.REVENUE, property_id=jane.id,
                     amount=7777, date_of_payment=date(2025, 12, 5))

    body = client.get("/transactions/summary").json()
    assert body["ytd_year"] == 2026
    assert body["ytd_net"] == 9500.0
    assert body["ytd_by_owner"] == [
        {"owner": "Jane Cooper", "revenue": 9000.0, "expenses": 1000.0, "net": 8000.0},
        {"owner": "Dana Levi", "revenue": 2000.0, "expenses": 0.0, "net": 2000.0},
        {"owner": None, "revenue": 0.0, "expenses": 500.0, "net": -500.0},
    ]


@freeze_time("2026-06-15")
def test_summary_ytd_counts_revenue_under_month_for(client, db_session):
    """Rent paid in Dec *for* January belongs to the new year, like the report."""
    prop = make_property(db_session, property_owner="Jane Cooper")
    make_transaction(db_session, type=TransactionTypeEnum.REVENUE, property_id=prop.id,
                     amount=3000, date_of_payment=date(2025, 12, 28),
                     month_for=date(2026, 1, 1))

    body = client.get("/transactions/summary").json()
    assert body["ytd_net"] == 3000.0
    assert body["ytd_by_owner"] == [
        {"owner": "Jane Cooper", "revenue": 3000.0, "expenses": 0.0, "net": 3000.0},
    ]

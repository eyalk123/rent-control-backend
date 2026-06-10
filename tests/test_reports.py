"""Full-stack tests for /reports.

These assert the report endpoints generate successfully, record a ReportExport,
and stay owner-scoped. Exact PDF bytes / CSV layout are intentionally not pinned.
"""
from datetime import date

from tests.conftest import OWNER_A, OWNER_B
from tests.factories import (
    make_expense_category,
    make_property,
    make_transaction,
)
from app.models.transaction import TransactionTypeEnum


def _seed_year(db_session, owner_id=OWNER_A):
    prop = make_property(db_session, owner_id=owner_id, property_owner="Alice")
    cat = make_expense_category(db_session, owner_id=owner_id, name="Repairs")
    make_transaction(
        db_session,
        owner_id=owner_id,
        type=TransactionTypeEnum.REVENUE,
        property_id=prop.id,
        amount=8000,
        date_of_payment=date(2025, 3, 1),
        month_for=date(2025, 3, 1),
    )
    make_transaction(
        db_session,
        owner_id=owner_id,
        type=TransactionTypeEnum.EXPENSE,
        property_id=prop.id,
        amount=1500,
        date_of_payment=date(2025, 4, 1),
        categories=[cat],
        payment_method=None,
    )
    return prop


def test_income_expense_pdf(client, db_session):
    _seed_year(db_session)
    resp = client.get("/reports/income-expense", params={"year": 2025})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"


def test_income_expense_csv(client, db_session):
    _seed_year(db_session)
    resp = client.get("/reports/income-expense", params={"year": 2025, "format": "csv"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "Income & Expense Report" in resp.content.decode("utf-8-sig")


def test_expense_log_pdf(client, db_session):
    _seed_year(db_session)
    resp = client.get("/reports/expense-log", params={"year": 2025})
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_expense_log_csv(client, db_session):
    _seed_year(db_session)
    resp = client.get("/reports/expense-log", params={"year": 2025, "format": "csv"})
    assert resp.status_code == 200
    assert "Expense Log" in resp.content.decode("utf-8-sig")


def test_report_records_export_in_history(client, db_session):
    _seed_year(db_session)
    assert client.get("/reports/history").json() == []

    client.get("/reports/income-expense", params={"year": 2025, "format": "csv"})
    history = client.get("/reports/history").json()
    assert len(history) == 1
    assert history[0]["report_type"] == "income_expense"
    assert history[0]["format"] == "csv"
    assert history[0]["year"] == 2025


def test_report_invalid_format_rejected(client):
    resp = client.get("/reports/income-expense", params={"year": 2025, "format": "xml"})
    assert resp.status_code == 422


def test_report_year_out_of_range_rejected(client):
    assert client.get("/reports/income-expense", params={"year": 1800}).status_code == 422


def test_history_owner_scoped(client_factory, db_session):
    _seed_year(db_session, owner_id=OWNER_B)
    client_b = client_factory(OWNER_B)
    client_b.get("/reports/income-expense", params={"year": 2025, "format": "csv"})

    client_a = client_factory(OWNER_A)
    assert client_a.get("/reports/history").json() == []


def test_delete_history(client, db_session):
    _seed_year(db_session)
    client.get("/reports/income-expense", params={"year": 2025, "format": "csv"})
    export_id = client.get("/reports/history").json()[0]["id"]

    assert client.delete(f"/reports/history/{export_id}").status_code == 204
    assert client.get("/reports/history").json() == []


def test_delete_history_not_found(client):
    assert client.delete("/reports/history/999").status_code == 404


def test_delete_history_other_owner(client_factory, db_session):
    _seed_year(db_session, owner_id=OWNER_B)
    client_b = client_factory(OWNER_B)
    client_b.get("/reports/income-expense", params={"year": 2025, "format": "csv"})
    export_id = client_b.get("/reports/history").json()[0]["id"]

    client_a = client_factory(OWNER_A)
    assert client_a.delete(f"/reports/history/{export_id}").status_code == 404

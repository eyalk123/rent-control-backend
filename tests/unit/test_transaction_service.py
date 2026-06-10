"""Unit tests for TransactionService (real repos over the test session)."""
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from freezegun import freeze_time

from app.repositories.expense_category_repository import ExpenseCategoryRepository
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.repositories.supplier_repository import SupplierRepository
from app.repositories.transaction_repository import TransactionRepository
from app.schemas.transaction import TransactionCreateExpense, TransactionCreateRevenue
from app.services.transaction_service import TransactionService
from tests.conftest import OWNER_A
from tests.factories import (
    make_expense_category,
    make_property,
    make_renter,
    make_supplier,
    make_transaction,
)
from app.models.transaction import TransactionTypeEnum


def _service(db_session):
    return TransactionService(
        transaction_repository=TransactionRepository(db_session),
        property_repository=PropertyRepository(db_session),
        renter_repository=RenterRepository(db_session),
        expense_category_repository=ExpenseCategoryRepository(db_session),
        supplier_repository=SupplierRepository(db_session),
    )


def test_create_revenue_stores_decimal_amount_and_snapshots(db_session):
    svc = _service(db_session)
    prop = make_property(db_session, address="1 St", city="Town")
    renter = make_renter(db_session, property_id=prop.id, first_name="Jo", last_name="Smith")
    data = TransactionCreateRevenue(
        property_id=prop.id, renter_id=renter.id, amount=1234.5, month_for=date(2026, 5, 1)
    )
    read = svc.create_revenue(data, OWNER_A)
    assert read.amount == Decimal("1234.5")
    assert read.property_name == "1 St, Town"
    assert read.renter_name == "Jo Smith"


def test_create_revenue_defaults_date_of_payment_to_today(db_session):
    svc = _service(db_session)
    prop = make_property(db_session)
    with freeze_time("2026-03-09"):
        read = svc.create_revenue(
            TransactionCreateRevenue(property_id=prop.id, amount=1, month_for=date(2026, 3, 1)),
            OWNER_A,
        )
    assert read.date_of_payment == date(2026, 3, 9)


def test_create_revenue_missing_property_raises_404(db_session):
    svc = _service(db_session)
    with pytest.raises(HTTPException) as exc:
        svc.create_revenue(
            TransactionCreateRevenue(property_id=999, amount=1, month_for=date(2026, 5, 1)),
            OWNER_A,
        )
    assert exc.value.status_code == 404


def test_create_expense_sets_categories_relationship(db_session):
    svc = _service(db_session)
    prop = make_property(db_session)
    cat1 = make_expense_category(db_session, name="C1")
    cat2 = make_expense_category(db_session, name="C2")
    data = TransactionCreateExpense(
        property_id=prop.id,
        amount=50,
        date_of_payment=date(2026, 4, 1),
        payment_method="cash",
        category_ids=[cat1.id, cat2.id],
    )
    read = svc.create_expense(data, OWNER_A)
    assert set(read.category_ids) == {cat1.id, cat2.id}
    assert read.category_name in ("C1", "C2")


def test_transaction_to_read_falls_back_to_snapshot_when_property_deleted(db_session):
    svc = _service(db_session)
    # Transaction with no FK property but a denormalized snapshot.
    txn = make_transaction(
        db_session,
        type=TransactionTypeEnum.REVENUE,
        property_id=None,
        property_address="Snapshot Addr",
        renter_name="Snapshot Renter",
    )
    read = svc.get_transaction(txn.id, OWNER_A)
    assert read.property_name == "Snapshot Addr"
    assert read.renter_name == "Snapshot Renter"


@freeze_time("2026-06-15")
def test_summary_all_zero_when_no_data(db_session):
    svc = _service(db_session)
    summary = svc.get_summary(OWNER_A)
    buckets = summary.six_month_buckets
    assert len(buckets) == 6
    assert all(b.revenue == 0 and b.expenses == 0 and b.profit == 0 for b in buckets)
    assert [b.key for b in buckets] == [
        "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
    ]


@freeze_time("2026-01-15")
def test_summary_spans_year_boundary(db_session):
    svc = _service(db_session)
    prop = make_property(db_session)
    make_transaction(
        db_session,
        type=TransactionTypeEnum.REVENUE,
        property_id=prop.id,
        amount=1000,
        date_of_payment=date(2025, 8, 10),
    )
    buckets = svc.get_summary(OWNER_A).six_month_buckets
    keys = [b.key for b in buckets]
    assert keys == ["2025-08", "2025-09", "2025-10", "2025-11", "2025-12", "2026-01"]
    assert buckets[0].revenue == 1000.0


def test_update_expense_empty_category_list_raises_400(db_session):
    svc = _service(db_session)
    prop = make_property(db_session)
    cat = make_expense_category(db_session)
    txn = make_transaction(
        db_session, type=TransactionTypeEnum.EXPENSE, property_id=prop.id, categories=[cat]
    )
    from app.schemas.transaction import TransactionUpdateExpense

    with pytest.raises(HTTPException) as exc:
        svc.update_expense(txn.id, TransactionUpdateExpense(category_ids=[]), OWNER_A)
    assert exc.value.status_code == 400


def test_update_revenue_changes_property_and_refreshes_snapshot(db_session):
    svc = _service(db_session)
    p1 = make_property(db_session, address="Old", city="X")
    p2 = make_property(db_session, address="New", city="Y")
    txn = make_transaction(
        db_session, type=TransactionTypeEnum.REVENUE, property_id=p1.id,
        property_address="Old, X",
    )
    from app.schemas.transaction import TransactionUpdateRevenue

    read = svc.update_revenue(
        txn.id, TransactionUpdateRevenue(property_id=p2.id), OWNER_A
    )
    assert read.property_id == p2.id
    assert read.property_name == "New, Y"


def test_update_revenue_property_not_found_raises_404(db_session):
    svc = _service(db_session)
    prop = make_property(db_session)
    txn = make_transaction(db_session, type=TransactionTypeEnum.REVENUE, property_id=prop.id)
    from app.schemas.transaction import TransactionUpdateRevenue

    with pytest.raises(HTTPException) as exc:
        svc.update_revenue(txn.id, TransactionUpdateRevenue(property_id=999), OWNER_A)
    assert exc.value.status_code == 404


def test_update_revenue_sets_then_clears_renter(db_session):
    svc = _service(db_session)
    prop = make_property(db_session)
    renter = make_renter(db_session, property_id=prop.id, first_name="Ann", last_name="Lee")
    txn = make_transaction(db_session, type=TransactionTypeEnum.REVENUE, property_id=prop.id)
    from app.schemas.transaction import TransactionUpdateRevenue

    # Assign renter
    read = svc.update_revenue(
        txn.id, TransactionUpdateRevenue(renter_id=renter.id), OWNER_A
    )
    assert read.renter_id == renter.id
    assert read.renter_name == "Ann Lee"

    # Explicitly null it out
    read = svc.update_revenue(
        txn.id, TransactionUpdateRevenue(renter_id=None), OWNER_A
    )
    assert read.renter_id is None
    assert read.renter_name is None


def test_update_revenue_updates_scalar_fields(db_session):
    svc = _service(db_session)
    prop = make_property(db_session)
    txn = make_transaction(db_session, type=TransactionTypeEnum.REVENUE, property_id=prop.id)
    from app.schemas.transaction import TransactionUpdateRevenue

    read = svc.update_revenue(
        txn.id,
        TransactionUpdateRevenue(
            amount=999,
            date_of_payment=date(2026, 7, 1),
            month_for=date(2026, 7, 1),
            payment_method="cash",
            notes="note",
        ),
        OWNER_A,
    )
    assert read.amount == Decimal("999")
    assert read.date_of_payment == date(2026, 7, 1)
    assert read.month_for == date(2026, 7, 1)
    assert read.payment_method.value == "cash"
    assert read.notes == "note"


def test_update_revenue_missing_transaction_returns_none(db_session):
    svc = _service(db_session)
    from app.schemas.transaction import TransactionUpdateRevenue

    assert svc.update_revenue(999, TransactionUpdateRevenue(amount=1), OWNER_A) is None


def test_update_expense_changes_supplier_and_clears_it(db_session):
    svc = _service(db_session)
    prop = make_property(db_session)
    cat = make_expense_category(db_session)
    supplier = make_supplier(db_session, categories=[cat])
    txn = make_transaction(
        db_session, type=TransactionTypeEnum.EXPENSE, property_id=prop.id, categories=[cat]
    )
    from app.schemas.transaction import TransactionUpdateExpense

    read = svc.update_expense(
        txn.id, TransactionUpdateExpense(supplier_id=supplier.id), OWNER_A
    )
    assert read.supplier_id == supplier.id

    read = svc.update_expense(
        txn.id, TransactionUpdateExpense(supplier_id=None), OWNER_A
    )
    assert read.supplier_id is None


def test_update_expense_unknown_supplier_raises_400(db_session):
    svc = _service(db_session)
    prop = make_property(db_session)
    cat = make_expense_category(db_session)
    txn = make_transaction(
        db_session, type=TransactionTypeEnum.EXPENSE, property_id=prop.id, categories=[cat]
    )
    from app.schemas.transaction import TransactionUpdateExpense

    with pytest.raises(HTTPException) as exc:
        svc.update_expense(txn.id, TransactionUpdateExpense(supplier_id=999), OWNER_A)
    assert exc.value.status_code == 400


def test_create_expense_supplier_category_mismatch_raises_400(db_session):
    svc = _service(db_session)
    prop = make_property(db_session)
    cat_used = make_expense_category(db_session, name="Used")
    cat_other = make_expense_category(db_session, name="Other")
    supplier = make_supplier(db_session, categories=[cat_other])
    data = TransactionCreateExpense(
        property_id=prop.id,
        amount=10,
        date_of_payment=date(2026, 4, 1),
        payment_method="bit",
        category_ids=[cat_used.id],
        supplier_id=supplier.id,
    )
    with pytest.raises(HTTPException) as exc:
        svc.create_expense(data, OWNER_A)
    assert exc.value.status_code == 400

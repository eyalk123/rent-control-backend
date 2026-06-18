"""Unit tests for SupplierService category-validation logic."""
import pytest
from fastapi import HTTPException

from app.repositories.expense_category_repository import ExpenseCategoryRepository
from app.repositories.supplier_repository import SupplierRepository
from app.schemas.supplier import SupplierCreate, SupplierUpdate
from app.services.supplier_service import SupplierService
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_expense_category, make_supplier


def _service(db_session):
    return SupplierService(
        SupplierRepository(db_session), ExpenseCategoryRepository(db_session)
    )


def test_validate_allows_predefined_category(db_session):
    svc = _service(db_session)
    predefined = make_expense_category(db_session, owner_id=None, key="utilities")
    # Should not raise.
    svc._validate_category_ids([predefined.id], OWNER_A)


def test_validate_allows_own_category(db_session):
    svc = _service(db_session)
    own = make_expense_category(db_session, owner_id=OWNER_A)
    svc._validate_category_ids([own.id], OWNER_A)


def test_validate_rejects_foreign_category(db_session):
    svc = _service(db_session)
    foreign = make_expense_category(db_session, owner_id=OWNER_B)
    with pytest.raises(HTTPException) as exc:
        svc._validate_category_ids([foreign.id], OWNER_A)
    assert exc.value.status_code == 400


def test_validate_rejects_unknown_category(db_session):
    svc = _service(db_session)
    with pytest.raises(HTTPException) as exc:
        svc._validate_category_ids([999], OWNER_A)
    assert exc.value.status_code == 400


def test_create_supplier_trims_name_and_sets_active(db_session):
    svc = _service(db_session)
    cat = make_expense_category(db_session)
    read = svc.create_supplier(
        SupplierCreate(name="  Spaced  ", category_ids=[cat.id]), OWNER_A
    )
    assert read.name == "Spaced"
    assert read.is_active is True
    assert read.category_ids == [cat.id]


def test_update_supplier_returns_none_when_missing(db_session):
    svc = _service(db_session)
    assert svc.update_supplier(999, SupplierUpdate(name="x"), OWNER_A) is None


def test_update_supplier_replaces_categories(db_session):
    svc = _service(db_session)
    c1 = make_expense_category(db_session, name="C1")
    c2 = make_expense_category(db_session, name="C2")
    supplier = make_supplier(db_session, categories=[c1])
    read = svc.update_supplier(
        supplier.id, SupplierUpdate(category_ids=[c2.id]), OWNER_A
    )
    assert read.category_ids == [c2.id]


def test_update_supplier_clears_optional_fields_when_sent_null(db_session):
    svc = _service(db_session)
    supplier = make_supplier(
        db_session, phone="050", email="a@b.com", notes="n", bank_account="12/345/678"
    )
    read = svc.update_supplier(
        supplier.id,
        SupplierUpdate(phone=None, email=None, notes=None, bank_account=None),
        OWNER_A,
    )
    assert read.phone is None
    assert read.email is None
    assert read.notes is None
    assert read.bank_account is None


def test_update_supplier_leaves_omitted_fields_untouched(db_session):
    svc = _service(db_session)
    supplier = make_supplier(db_session, phone="050", bank_account="12/345/678")
    # Only name is sent; phone/bank_account must be preserved.
    read = svc.update_supplier(supplier.id, SupplierUpdate(name="Renamed"), OWNER_A)
    assert read.name == "Renamed"
    assert read.phone == "050"
    assert read.bank_account == "12/345/678"


def test_update_supplier_ignores_empty_name(db_session):
    svc = _service(db_session)
    supplier = make_supplier(db_session, name="Original")
    read = svc.update_supplier(supplier.id, SupplierUpdate(name="   "), OWNER_A)
    assert read.name == "Original"

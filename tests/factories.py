"""Direct-insert helpers for seeding test data.

These bypass the service layer and write models straight to the session, mirroring
the JSON-as-Text encoding the services use (e.g. ``lease_years``). Use them to set
up state; use the API client to exercise the code under test.
"""
import json
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.expense_category import ExpenseCategory
from app.models.property import Property, PropertyTypeEnum
from app.models.renter import Renter
from app.models.supplier import Supplier
from app.models.transaction import (
    PaymentMethodEnum,
    Transaction,
    TransactionTypeEnum,
)
from tests.conftest import OWNER_A

DEFAULT_LEASE_YEARS = [{"amount": 12000.0, "type": "contract"}]


def make_property(session: Session, owner_id: str = OWNER_A, **kw) -> Property:
    defaults = dict(
        owner_id=owner_id,
        address="1 Main St",
        city="Tel Aviv",
        zip_code="60000",
        type=PropertyTypeEnum.APARTMENT,
        sq_ft=80,
        purchase_price=1_000_000.0,
    )
    defaults.update(kw)
    prop = Property(**defaults)
    session.add(prop)
    session.commit()
    session.refresh(prop)
    return prop


def make_renter(
    session: Session,
    owner_id: str = OWNER_A,
    property_id: int | None = None,
    lease_years: list[dict] | None = None,
    **kw,
) -> Renter:
    defaults = dict(
        owner_id=owner_id,
        property_id=property_id,
        first_name="John",
        last_name="Doe",
        phone="0500000000",
        email="john@example.com",
        lease_years=json.dumps(lease_years or DEFAULT_LEASE_YEARS),
    )
    defaults.update(kw)
    renter = Renter(**defaults)
    session.add(renter)
    session.commit()
    session.refresh(renter)
    return renter


def make_expense_category(
    session: Session,
    owner_id: str | None = OWNER_A,
    name: str = "Repairs",
    key: str | None = None,
    sort_order: int = 0,
    is_active: bool = True,
) -> ExpenseCategory:
    cat = ExpenseCategory(
        owner_id=owner_id,
        name=name,
        key=key,
        sort_order=sort_order,
        is_active=is_active,
    )
    session.add(cat)
    session.commit()
    session.refresh(cat)
    return cat


def make_supplier(
    session: Session,
    owner_id: str = OWNER_A,
    name: str = "Acme Plumbing",
    categories: list[ExpenseCategory] | None = None,
    is_active: bool = True,
    **kw,
) -> Supplier:
    supplier = Supplier(owner_id=owner_id, name=name, is_active=is_active, **kw)
    if categories:
        supplier.categories = categories
    session.add(supplier)
    session.commit()
    session.refresh(supplier)
    return supplier


def make_transaction(
    session: Session,
    owner_id: str = OWNER_A,
    type: TransactionTypeEnum = TransactionTypeEnum.REVENUE,
    property_id: int | None = None,
    renter_id: int | None = None,
    amount: float = 5000.0,
    date_of_payment: date | None = None,
    month_for: date | None = None,
    currency_code: str = "ILS",
    categories: list[ExpenseCategory] | None = None,
    payment_method: PaymentMethodEnum | None = None,
    **kw,
) -> Transaction:
    txn = Transaction(
        owner_id=owner_id,
        type=type,
        property_id=property_id,
        renter_id=renter_id,
        amount=Decimal(str(amount)),
        date_of_payment=date_of_payment or date.today(),
        month_for=month_for,
        currency_code=currency_code,
        payment_method=payment_method,
        **kw,
    )
    if categories:
        txn.categories = categories
        txn.category_id = categories[0].id
    session.add(txn)
    session.commit()
    session.refresh(txn)
    return txn

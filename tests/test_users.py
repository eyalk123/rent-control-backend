"""Full-stack tests for /users/me (account deletion cascade)."""
from sqlalchemy import select

from tests.conftest import OWNER_A, OWNER_B
from tests.factories import (
    make_expense_category,
    make_property,
    make_renter,
    make_supplier,
    make_transaction,
)
from app.models.expense_category import ExpenseCategory
from app.models.property import Property
from app.models.renter import Renter
from app.models.supplier import Supplier
from app.models.transaction import Transaction


def test_delete_account_cascades_owner_data(client, db_session):
    prop = make_property(db_session, owner_id=OWNER_A)
    make_renter(db_session, owner_id=OWNER_A, property_id=prop.id)
    cat = make_expense_category(db_session, owner_id=OWNER_A)
    make_supplier(db_session, owner_id=OWNER_A, categories=[cat])
    make_transaction(db_session, owner_id=OWNER_A, property_id=prop.id)

    resp = client.delete("/users/me")
    assert resp.status_code == 200
    assert resp.json() == {"success": True}

    db_session.expire_all()
    assert db_session.scalars(select(Property).where(Property.owner_id == OWNER_A)).all() == []
    assert db_session.scalars(select(Renter).where(Renter.owner_id == OWNER_A)).all() == []
    assert db_session.scalars(select(Supplier).where(Supplier.owner_id == OWNER_A)).all() == []
    assert db_session.scalars(select(ExpenseCategory).where(ExpenseCategory.owner_id == OWNER_A)).all() == []
    assert db_session.scalars(select(Transaction).where(Transaction.owner_id == OWNER_A)).all() == []


def test_delete_account_leaves_other_owner_untouched(client_factory, db_session):
    prop_b = make_property(db_session, owner_id=OWNER_B)
    make_renter(db_session, owner_id=OWNER_B, property_id=prop_b.id)

    client_a = client_factory(OWNER_A)
    assert client_a.delete("/users/me").status_code == 200

    db_session.expire_all()
    assert len(db_session.scalars(select(Property).where(Property.owner_id == OWNER_B)).all()) == 1
    assert len(db_session.scalars(select(Renter).where(Renter.owner_id == OWNER_B)).all()) == 1

"""Deleting a record leaves a trace behind.

The log is a trace, not a copy: enough to answer "what happened to that renter?" without
reintroducing the deleted data (and its read-path risks) through the back door.
"""
from sqlalchemy import select

from app.models.activity_log import ActivityLog
from app.models.renter import Renter
from app.models.transaction import Transaction
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_property, make_renter, make_transaction


def _entries(db_session, owner_id=OWNER_A) -> list[ActivityLog]:
    return list(
        db_session.scalars(
            select(ActivityLog).where(ActivityLog.owner_id == owner_id).order_by(ActivityLog.id)
        ).all()
    )


def test_deleting_a_property_is_recorded(client, db_session):
    prop = make_property(db_session, address="רחוב הרצל 12", city="תל אביב")

    assert client.delete(f"/properties/{prop.id}").status_code in (200, 204)

    db_session.expire_all()
    entry = _entries(db_session)[0]
    assert (entry.action, entry.entity_type, entry.entity_id) == ("delete", "property", prop.id)
    assert entry.label == "רחוב הרצל 12, תל אביב"


def test_deleting_a_renter_records_their_name(client, db_session):
    prop = make_property(db_session)
    renter = make_renter(db_session, property_id=prop.id, first_name="שרה", last_name="כהן")

    assert client.delete(f"/renters/{renter.id}").status_code in (200, 204)

    db_session.expire_all()
    entry = _entries(db_session)[0]
    assert (entry.entity_type, entry.entity_id) == ("renter", renter.id)
    assert entry.label == "שרה כהן"


def test_deleting_a_transaction_records_the_amount(client, db_session):
    prop = make_property(db_session)
    txn = make_transaction(db_session, property_id=prop.id, amount=5250.5)

    assert client.delete(f"/transactions/{txn.id}").status_code in (200, 204)

    db_session.expire_all()
    entry = _entries(db_session)[0]
    assert (entry.entity_type, entry.entity_id) == ("transaction", txn.id)
    assert entry.details["amount"] == "5250.50"
    assert entry.details["type"] == "revenue"


def test_the_log_is_a_trace_not_a_copy(client, db_session):
    """The deleted row is really gone — the log records that it happened, nothing more."""
    prop = make_property(db_session)
    renter = make_renter(db_session, property_id=prop.id, phone="0500000000", email="a@b.com")

    client.delete(f"/renters/{renter.id}")

    db_session.expire_all()
    assert db_session.scalars(select(Renter).where(Renter.id == renter.id)).all() == []
    entry = _entries(db_session)[0]
    stored = f"{entry.label} {entry.details}"
    assert "0500000000" not in stored
    assert "a@b.com" not in stored


def test_a_failed_delete_records_nothing(client, db_session):
    """404s must not write a row — the log should only ever describe things that happened."""
    assert client.delete("/renters/9999").status_code == 404
    assert client.delete("/transactions/9999").status_code == 404
    assert client.delete("/properties/9999").status_code == 404

    db_session.expire_all()
    assert _entries(db_session) == []


def test_log_is_owner_scoped(client_factory, db_session):
    prop_b = make_property(db_session, owner_id=OWNER_B)
    txn_b = make_transaction(db_session, owner_id=OWNER_B, property_id=prop_b.id)

    client_b = client_factory(OWNER_B)
    client_b.delete(f"/transactions/{txn_b.id}")

    db_session.expire_all()
    assert len(_entries(db_session, OWNER_B)) == 1
    assert _entries(db_session, OWNER_A) == []
    # And the transaction really is gone, not merely hidden.
    assert db_session.scalars(select(Transaction).where(Transaction.id == txn_b.id)).all() == []

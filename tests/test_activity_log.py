"""Changing a record leaves a trace behind.

The log is a trace, not a copy: enough to answer "what happened to that renter?" without
reintroducing the deleted data (and its read-path risks) through the back door.
"""
import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.activity_log import ActivityLog
from app.models.renter import Renter
from app.models.transaction import Transaction, TransactionTypeEnum
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import (
    make_expense_category,
    make_property,
    make_renter,
    make_transaction,
)


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


# --- edits ------------------------------------------------------------------------
#
# `updated_at` is overwritten in place, so it can only ever show the *last* edit. An
# owner who spends a week correcting rents and phone numbers leaves no other trace, and
# `update` rows are what make that week visible. Two rules do the work: what the client
# *sent* is not what *changed*, and `details` carries field names and never values.


def _updates(db_session, owner_id=OWNER_A) -> list[ActivityLog]:
    return [e for e in _entries(db_session, owner_id) if e.action == "update"]


def test_editing_a_property_records_the_changed_field(client, db_session):
    prop = make_property(db_session, address="רחוב הרצל 12", city="תל אביב")

    assert client.patch(f"/properties/{prop.id}", json={"city": "חיפה"}).status_code == 200

    db_session.expire_all()
    entry = _updates(db_session)[0]
    assert (entry.action, entry.entity_type, entry.entity_id) == ("update", "property", prop.id)
    assert entry.details == {"fields": ["city"]}
    # The label is the address as it was *before* the edit, matching the delete path.
    assert entry.label == "רחוב הרצל 12, תל אביב"


def test_resubmitting_an_unchanged_property_records_nothing(client, db_session):
    """The clients PATCH the whole form back, so "what was sent" is not "what changed"."""
    prop = make_property(db_session, address="1 Main St", city="Tel Aviv", sq_ft=80)

    body = {
        "address": "1 Main St",
        "city": "Tel Aviv",
        "zip_code": "60000",
        "type": "apartment",
        "sq_ft": 80,
        "purchase_price": 1_000_000.0,
    }
    assert client.patch(f"/properties/{prop.id}", json=body).status_code == 200

    db_session.expire_all()
    assert _updates(db_session) == []


def test_unchanged_parking_numbers_are_not_a_change(client, db_session):
    """`parking_numbers` is stored JSON-encoded. Compared before that encoding, the list
    never equals the string and every single save would look like an edit."""
    prop = make_property(db_session, parking_numbers=json.dumps(["A-1", "A-2"]))

    body = {"parking_numbers": ["A-1", "A-2"], "type": "apartment"}
    assert client.patch(f"/properties/{prop.id}", json=body).status_code == 200

    db_session.expire_all()
    assert _updates(db_session) == []

    # And a real change to the same field still registers.
    assert client.patch(
        f"/properties/{prop.id}", json={"parking_numbers": ["A-1"]}
    ).status_code == 200
    db_session.expire_all()
    assert _updates(db_session)[0].details == {"fields": ["parking_numbers"]}


def test_a_multi_field_edit_names_only_what_moved(client, db_session):
    prop = make_property(db_session, city="Tel Aviv", sq_ft=80, purchase_price=1_000_000.0)

    body = {
        "city": "Haifa",                # changed
        "sq_ft": 95,                    # changed
        "purchase_price": 1_000_000.0,  # resubmitted unchanged
    }
    assert client.patch(f"/properties/{prop.id}", json=body).status_code == 200

    db_session.expire_all()
    entries = _updates(db_session)
    assert len(entries) == 1
    assert entries[0].details["fields"] == ["city", "sq_ft"]


def test_editing_a_renter_records_the_changed_fields_only(client, db_session):
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        first_name="שרה",
        last_name="כהן",
        phone="0500000000",
        base_rent=5000.0,
    )

    body = {"phone": "0521111111", "base_rent": 5500.0, "first_name": "שרה"}
    assert client.patch(f"/renters/{renter.id}", json=body).status_code == 200

    db_session.expire_all()
    entry = _updates(db_session)[0]
    assert (entry.entity_type, entry.entity_id) == ("renter", renter.id)
    assert entry.details["fields"] == ["base_rent", "phone"]
    assert entry.label == "שרה כהן"


def test_renter_server_owned_fields_are_not_an_edit(client, db_session):
    """`lease_end`, `contract_end` and the lease amounts are recomputed on every save.
    They move on their own, so they are not evidence that anybody did anything."""
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        lease_start=date(2024, 1, 1),
        base_rent=5000.0,
        rent_escalation_mode="none",
    )
    # Clear the derived dates so any recomputation shows up as a difference.
    renter.lease_end = None
    renter.contract_end = None
    db_session.commit()

    body = {"lease_start": "2024-01-01", "base_rent": 5000.0}
    assert client.patch(f"/renters/{renter.id}", json=body).status_code == 200

    db_session.expire_all()
    assert _updates(db_session) == []
    # The recomputation really did happen — this is not a no-op request.
    assert db_session.get(Renter, renter.id).lease_end is not None


def test_editing_a_transaction_records_the_changed_field(client, db_session):
    prop = make_property(db_session, address="1 Main St", city="Tel Aviv")
    txn = make_transaction(
        db_session, property_id=prop.id, amount=5000.0, property_address="1 Main St, Tel Aviv"
    )

    assert client.patch(
        f"/transactions/revenue/{txn.id}", json={"amount": 5250.5}
    ).status_code == 200

    db_session.expire_all()
    entry = _updates(db_session)[0]
    assert (entry.entity_type, entry.entity_id) == ("transaction", txn.id)
    assert entry.details == {"fields": ["amount"]}
    assert entry.label == "1 Main St, Tel Aviv"


def test_resubmitting_an_unchanged_transaction_records_nothing(client, db_session):
    prop = make_property(db_session)
    txn = make_transaction(
        db_session,
        property_id=prop.id,
        amount=5000.0,
        date_of_payment=date(2024, 3, 10),
        property_address="1 Main St, Tel Aviv",
    )

    body = {"amount": 5000.0, "date_of_payment": "2024-03-10", "property_id": prop.id}
    assert client.patch(f"/transactions/revenue/{txn.id}", json=body).status_code == 200

    db_session.expire_all()
    assert _updates(db_session) == []


def test_an_expense_category_swap_is_recorded(client, db_session):
    """The categories live on a relationship, not a column — the field diff cannot see
    them, so they are reported explicitly."""
    prop = make_property(db_session)
    repairs = make_expense_category(db_session, name="Repairs")
    cleaning = make_expense_category(db_session, name="Cleaning")
    txn = make_transaction(
        db_session,
        property_id=prop.id,
        type=TransactionTypeEnum.EXPENSE,
        categories=[repairs],
    )

    assert client.patch(
        f"/transactions/expense/{txn.id}", json={"category_ids": [cleaning.id]}
    ).status_code == 200

    db_session.expire_all()
    assert _updates(db_session)[0].details == {"fields": ["category_ids"]}


def test_details_never_carries_a_value(client, db_session):
    """The one hard rule: field names, never the values behind them. This table is read
    by analytics queries, and a phone number must not be sitting in their path."""
    prop = make_property(db_session)
    renter = make_renter(db_session, property_id=prop.id, phone="0500000000")

    assert client.patch(
        f"/renters/{renter.id}",
        json={"phone": "0521111111", "email": "new@example.com"},
    ).status_code == 200

    db_session.expire_all()
    entry = _updates(db_session)[0]
    # The shape itself is the guarantee: one key, whose value is a list of plain strings
    # that are all field names on the model.
    assert list(entry.details) == ["fields"]
    assert entry.details["fields"] == ["email", "phone"]
    assert all(isinstance(f, str) and hasattr(Renter, f) for f in entry.details["fields"])
    stored = str(entry.details)
    assert "0521111111" not in stored and "0500000000" not in stored
    assert "new@example.com" not in stored


def test_a_rejected_edit_leaves_no_log_row(client, db_session):
    """The log row rides the same transaction as the change it describes, so an edit
    that never happened cannot leave one behind."""
    prop = make_property(db_session)
    txn = make_transaction(db_session, property_id=prop.id, amount=5000.0)

    # Rejected inside the service, after the fields dict is built.
    assert client.patch(
        f"/transactions/revenue/{txn.id}", json={"amount": 6000.0, "renter_id": 9999}
    ).status_code == 400
    # And a missing target, which never gets as far as a comparison.
    assert client.patch("/properties/9999", json={"city": "Haifa"}).status_code == 404
    assert client.patch("/renters/9999", json={"phone": "0521111111"}).status_code == 404

    db_session.expire_all()
    assert _entries(db_session) == []
    assert db_session.get(Transaction, txn.id).amount == Decimal("5000.00")


def test_the_log_row_is_rolled_back_with_a_failed_write(client_factory, db_session):
    """If the write itself explodes, the trace goes with it — they are one transaction."""
    from app.repositories.transaction_repository import TransactionRepository

    prop = make_property(db_session)
    txn = make_transaction(db_session, property_id=prop.id, amount=5000.0)
    client = client_factory(OWNER_A)

    def _boom(*args, **kwargs):
        raise RuntimeError("write failed")

    original = TransactionRepository.update
    TransactionRepository.update = _boom
    try:
        with pytest.raises(RuntimeError):
            client.patch(f"/transactions/revenue/{txn.id}", json={"amount": 9999.0})
    finally:
        TransactionRepository.update = original

    db_session.rollback()
    db_session.expire_all()
    assert _entries(db_session) == []
    assert db_session.get(Transaction, txn.id).amount == Decimal("5000.00")

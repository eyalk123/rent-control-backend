"""Firebase Storage cleanup: account deletion, replaced documents, deleted records.

Blob names here use the real upload shape, `{entity_type}/{owner_id}/{uuid}/{filename}`
(storage.rules). The fakes once used `{owner_id}/...` — the same wrong shape as the code —
and every test passed while account deletion removed no file at all.
"""
from urllib.parse import quote

import pytest

from app.models.property_file import PropertyFile
from app.models.transaction import TransactionTypeEnum
from app.services import firebase_storage
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_expense_category, make_property, make_renter, make_transaction


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket = bucket
        self.name = name

    def delete(self):
        self.bucket.deleted.append(self.name)


class FakeBucket:
    def __init__(self, names=()):
        self.names = list(names)
        self.deleted: list[str] = []

    def list_blobs(self, prefix):
        return [FakeBlob(self, n) for n in self.names if n.startswith(prefix)]

    def blob(self, name):
        return FakeBlob(self, name)


@pytest.fixture
def bucket(monkeypatch):
    fake = FakeBucket()
    monkeypatch.setattr(firebase_storage, "_get_bucket", lambda: fake)
    return fake


def url(path: str) -> str:
    return f"https://firebasestorage.googleapis.com/v0/b/bkt/o/{quote(path, safe='')}?alt=media&token=t"


def test_owner_blobs_covers_every_upload_prefix():
    bucket = FakeBucket([
        f"properties/{OWNER_A}/u1/photo.jpg",
        f"renters/{OWNER_A}/u2/lease.pdf",
        f"transactions/{OWNER_A}/u3/receipt.jpg",
        f"renters/{OWNER_B}/u4/lease.pdf",
    ])
    names = sorted(b.name for b in firebase_storage.owner_blobs(bucket, OWNER_A))
    assert names == [
        f"properties/{OWNER_A}/u1/photo.jpg",
        f"renters/{OWNER_A}/u2/lease.pdf",
        f"transactions/{OWNER_A}/u3/receipt.jpg",
    ]


def test_account_deletion_deletes_the_owners_files_and_only_theirs(client, bucket):
    bucket.names = [
        f"properties/{OWNER_A}/u1/photo.jpg",
        f"renters/{OWNER_A}/u2/lease.pdf",
        f"transactions/{OWNER_A}/u3/receipt.jpg",
        f"renters/{OWNER_B}/u4/lease.pdf",
    ]
    assert client.delete("/users/me").status_code == 200
    assert sorted(bucket.deleted) == sorted(bucket.names[:3])


def test_replacing_a_property_document_deletes_the_old_one(client, db_session, bucket):
    old = url(f"properties/{OWNER_A}/u1/old.pdf")
    prop = make_property(db_session, basic_contract_url=old)

    resp = client.patch(f"/properties/{prop.id}", json={
        "basic_contract_url": url(f"properties/{OWNER_A}/u2/new.pdf"),
    })

    assert resp.status_code == 200
    assert bucket.deleted == [f"properties/{OWNER_A}/u1/old.pdf"]


def test_an_unrelated_edit_deletes_nothing(client, db_session, bucket):
    prop = make_property(db_session, basic_contract_url=url(f"properties/{OWNER_A}/u1/a.pdf"))
    assert client.patch(f"/properties/{prop.id}", json={"city": "Haifa"}).status_code == 200
    assert bucket.deleted == []


def test_clearing_a_renter_contract_deletes_it(client, db_session, bucket):
    prop = make_property(db_session)
    renter = make_renter(db_session, property_id=prop.id, full_contract_url=url(f"renters/{OWNER_A}/u1/lease.pdf"))

    assert client.patch(f"/renters/{renter.id}", json={"full_contract_url": None}).status_code == 200
    assert bucket.deleted == [f"renters/{OWNER_A}/u1/lease.pdf"]


def test_deleting_a_renter_deletes_its_files(client, db_session, bucket):
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        full_contract_url=url(f"renters/{OWNER_A}/u1/lease.pdf"),
        id_image_url=url(f"renters/{OWNER_A}/u2/id.jpg"),
    )
    assert client.delete(f"/renters/{renter.id}").status_code == 204
    assert sorted(bucket.deleted) == [f"renters/{OWNER_A}/u1/lease.pdf", f"renters/{OWNER_A}/u2/id.jpg"]


def test_replacing_a_receipt_deletes_the_old_one(client, db_session, bucket):
    cat = make_expense_category(db_session)
    txn = make_transaction(
        db_session,
        type=TransactionTypeEnum.EXPENSE,
        categories=[cat],
        receipt_image_url=url(f"transactions/{OWNER_A}/u1/old.jpg"),
    )
    resp = client.patch(f"/transactions/expense/{txn.id}", json={
        "receipt_image_url": url(f"transactions/{OWNER_A}/u2/new.jpg"),
    })
    assert resp.status_code == 200
    assert bucket.deleted == [f"transactions/{OWNER_A}/u1/old.jpg"]


def test_deleting_a_transaction_deletes_its_receipt(client, db_session, bucket):
    txn = make_transaction(db_session, receipt_image_url=url(f"transactions/{OWNER_A}/u1/r.jpg"))
    assert client.delete(f"/transactions/{txn.id}").status_code == 204
    assert bucket.deleted == [f"transactions/{OWNER_A}/u1/r.jpg"]


def test_deleting_a_property_deletes_its_attached_files(client, db_session, bucket):
    prop = make_property(db_session, image_url=url(f"properties/{OWNER_A}/u1/photo.jpg"))
    db_session.add(PropertyFile(property_id=prop.id, url=url(f"properties/{OWNER_A}/u2/plan.pdf"), label="Plan"))
    db_session.commit()

    assert client.delete(f"/properties/{prop.id}").status_code == 204
    assert sorted(bucket.deleted) == [f"properties/{OWNER_A}/u1/photo.jpg", f"properties/{OWNER_A}/u2/plan.pdf"]


def test_deleting_a_property_file_deletes_the_file(client, db_session, bucket):
    prop = make_property(db_session)
    file = PropertyFile(property_id=prop.id, url=url(f"properties/{OWNER_A}/u1/plan.pdf"), label="Plan")
    db_session.add(file)
    db_session.commit()

    assert client.delete(f"/properties/{prop.id}/files/{file.id}").status_code == 204
    assert bucket.deleted == [f"properties/{OWNER_A}/u1/plan.pdf"]


def test_a_file_another_record_still_uses_is_kept(client, db_session, bucket):
    shared = url(f"renters/{OWNER_A}/u1/lease.pdf")
    prop = make_property(db_session)
    first = make_renter(db_session, property_id=prop.id, full_contract_url=shared)
    make_renter(db_session, property_id=prop.id, full_contract_url=shared)

    assert client.delete(f"/renters/{first.id}").status_code == 204
    assert bucket.deleted == []


def test_another_accounts_file_is_never_deleted(client, db_session, bucket):
    """A URL is client-supplied and the Admin SDK ignores storage.rules: pointing a record
    at someone else's file and deleting the record must not delete their file."""
    victim = url(f"renters/{OWNER_B}/u1/lease.pdf")
    prop = make_property(db_session, image_url=victim)

    assert client.delete(f"/properties/{prop.id}").status_code == 204
    assert bucket.deleted == []

"""Tests for the owner-profile mirror: throttled upsert repository, the resilient
get_current_owner dependency, and the /users/me profile endpoint + deletion cascade."""
from datetime import datetime, timedelta

from app.api.dependencies import get_current_owner
from app.models.owner import Owner
from app.repositories.owner_repository import LAST_SEEN_THROTTLE, OwnerRepository
from tests.conftest import OWNER_A


# --- Repository throttle logic ------------------------------------------------

def test_upsert_inserts_when_missing(db_session):
    repo = OwnerRepository(db_session)
    owner = repo.upsert("uid-1", "a@example.com", "Alice", "http://pic/1.png")

    assert owner.id == "uid-1"
    assert owner.email == "a@example.com"
    assert owner.display_name == "Alice"
    assert owner.picture_url == "http://pic/1.png"
    assert owner.last_seen_at is not None


def test_upsert_updates_when_claim_changes(db_session):
    repo = OwnerRepository(db_session)
    repo.upsert("uid-1", "a@example.com", "Alice", None)

    updated = repo.upsert("uid-1", "new@example.com", "Alice B", None)

    assert updated.email == "new@example.com"
    assert updated.display_name == "Alice B"


def test_upsert_skips_write_when_unchanged_within_window(db_session):
    repo = OwnerRepository(db_session)
    repo.upsert("uid-1", "a@example.com", "Alice", None)
    first_seen = db_session.get(Owner, "uid-1").last_seen_at

    # Same claims, still within the throttle window -> no write, last_seen unchanged.
    repo.upsert("uid-1", "a@example.com", "Alice", None)
    assert db_session.get(Owner, "uid-1").last_seen_at == first_seen


def test_upsert_refreshes_last_seen_when_stale(db_session):
    repo = OwnerRepository(db_session)
    repo.upsert("uid-1", "a@example.com", "Alice", None)

    stale = datetime.utcnow() - LAST_SEEN_THROTTLE - timedelta(minutes=1)
    owner = db_session.get(Owner, "uid-1")
    owner.last_seen_at = stale
    db_session.commit()

    repo.upsert("uid-1", "a@example.com", "Alice", None)
    assert db_session.get(Owner, "uid-1").last_seen_at > stale


# --- Dependency resilience ----------------------------------------------------

def test_get_current_owner_is_resilient_to_upsert_failure():
    class BoomRepo:
        def upsert(self, **kwargs):
            raise RuntimeError("db down")

    current_user = {"user_id": OWNER_A, "role": "owner", "email": None, "name": None, "picture": None}
    # Must not raise — the profile write is best-effort telemetry.
    assert get_current_owner(current_user, BoomRepo()) is current_user


# --- Endpoint -----------------------------------------------------------------

def test_get_my_profile_returns_row_created_by_auth(client):
    resp = client.get("/users/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == OWNER_A
    assert "email" in body and "last_seen_at" in body


def test_delete_account_removes_owner_profile(client, db_session):
    # A prior authenticated call creates the profile row via get_current_owner.
    assert client.get("/users/me").status_code == 200
    assert db_session.get(Owner, OWNER_A) is not None

    assert client.delete("/users/me").status_code == 200

    db_session.expire_all()
    assert db_session.get(Owner, OWNER_A) is None

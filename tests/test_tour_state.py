"""Tests for onboarding tour state: merge semantics, the seed/tour split, and the
/users/me/tour-state endpoints.

The behaviour worth protecting here is that a *seed* and its destination *tour* are
tracked apart. If those ever collapse into one set, naming a feature would silently
suppress the explanation of it, and the whole two-layer model quietly stops working.
"""
import json

from app.models.owner import Owner
from app.repositories.owner_repository import OwnerRepository
from app.schemas.tour_state import MAX_ENTRIES
from tests.conftest import OWNER_A


def _make_owner(db_session, uid=OWNER_A):
    owner = Owner(id=uid, email="a@example.com", display_name="Alice")
    db_session.add(owner)
    db_session.commit()
    return owner


# --- Repository ---------------------------------------------------------------

def test_defaults_to_seen_nothing(db_session):
    _make_owner(db_session)
    state = OwnerRepository(db_session).get_tour_state(OWNER_A)

    assert state == {"tours_seen": {}, "seeds_shown": {}, "tours_disabled": False}


def test_unknown_owner_reports_seen_nothing_rather_than_failing(db_session):
    # First launch can ask before the profile row exists; it must not 500.
    assert OwnerRepository(db_session).get_tour_state("nobody")["tours_seen"] == {}


def test_merge_adds_without_dropping_existing(db_session, monkeypatch):
    from datetime import datetime

    _make_owner(db_session)
    repo = OwnerRepository(db_session)

    repo.merge_tour_state(OWNER_A, tours_seen={"first-run": datetime(2026, 8, 1)})
    state = repo.merge_tour_state(OWNER_A, tours_seen={"lease-form": datetime(2026, 8, 2)})

    # The phone's progress survives the browser's write.
    assert set(state["tours_seen"]) == {"first-run", "lease-form"}


def test_seed_does_not_consume_its_destination_tour(db_session):
    from datetime import datetime

    _make_owner(db_session)
    repo = OwnerRepository(db_session)

    repo.merge_tour_state(OWNER_A, seeds_shown={"suppliers": datetime(2026, 8, 1)})
    state = repo.get_tour_state(OWNER_A)

    assert state["seeds_shown"] == {"suppliers": "2026-08-01T00:00:00"}
    assert state["tours_seen"] == {}, "seeing the seed must not mark the tour as seen"


def test_first_sighting_wins(db_session):
    from datetime import datetime

    _make_owner(db_session)
    repo = OwnerRepository(db_session)

    repo.merge_tour_state(OWNER_A, seeds_shown={"cpi": datetime(2026, 8, 1)})
    state = repo.merge_tour_state(OWNER_A, seeds_shown={"cpi": datetime(2026, 8, 20)})

    assert state["seeds_shown"]["cpi"] == "2026-08-01T00:00:00"


def test_reset_clears_both_maps_but_keeps_the_disable_flag(db_session):
    from datetime import datetime

    _make_owner(db_session)
    repo = OwnerRepository(db_session)
    repo.merge_tour_state(
        OWNER_A,
        tours_seen={"first-run": datetime(2026, 8, 1)},
        seeds_shown={"suppliers": datetime(2026, 8, 1)},
        tours_disabled=True,
    )

    state = repo.merge_tour_state(OWNER_A, reset=True)

    assert state["tours_seen"] == {}
    assert state["seeds_shown"] == {}
    assert state["tours_disabled"] is True


def test_reset_and_write_in_one_request(db_session):
    from datetime import datetime

    _make_owner(db_session)
    repo = OwnerRepository(db_session)
    repo.merge_tour_state(OWNER_A, tours_seen={"old": datetime(2026, 8, 1)})

    state = repo.merge_tour_state(
        OWNER_A, reset=True, tours_seen={"fresh": datetime(2026, 8, 2)}
    )

    assert state["tours_seen"] == {"fresh": "2026-08-02T00:00:00"}


def test_corrupt_blob_degrades_to_seen_nothing(db_session):
    owner = _make_owner(db_session)
    owner.tour_state = "{not json"
    db_session.commit()

    assert OwnerRepository(db_session).get_tour_state(OWNER_A)["tours_seen"] == {}


def test_row_is_bounded(db_session):
    from datetime import datetime, timedelta

    _make_owner(db_session)
    repo = OwnerRepository(db_session)
    base = datetime(2026, 1, 1)

    for i in range(MAX_ENTRIES + 25):
        repo.merge_tour_state(OWNER_A, tours_seen={f"t{i:04d}": base + timedelta(days=i)})

    state = repo.get_tour_state(OWNER_A)
    assert len(state["tours_seen"]) == MAX_ENTRIES
    assert "t0000" not in state["tours_seen"], "oldest entries are the ones dropped"


# --- Endpoints ----------------------------------------------------------------

def test_get_endpoint_returns_defaults(client, db_session):
    _make_owner(db_session)
    r = client.get("/users/me/tour-state")

    assert r.status_code == 200
    assert r.json() == {"tours_seen": {}, "seeds_shown": {}, "tours_disabled": False}


def test_patch_records_a_finished_tour(client, db_session):
    _make_owner(db_session)
    r = client.patch(
        "/users/me/tour-state",
        json={"tours_seen": {"first-run": "2026-08-24T10:00:00"}},
    )

    assert r.status_code == 200
    assert "first-run" in r.json()["tours_seen"]


def test_patch_merges_across_devices(client, db_session):
    _make_owner(db_session)
    client.patch("/users/me/tour-state", json={"tours_seen": {"first-run": "2026-08-24T10:00:00"}})
    r = client.patch("/users/me/tour-state", json={"seeds_shown": {"suppliers": "2026-08-24T11:00:00"}})

    body = r.json()
    assert "first-run" in body["tours_seen"]
    assert "suppliers" in body["seeds_shown"]


def test_patch_rejects_an_oversized_payload(client, db_session):
    _make_owner(db_session)
    r = client.patch(
        "/users/me/tour-state",
        json={"tours_seen": {f"t{i}": "2026-08-24T10:00:00" for i in range(MAX_ENTRIES + 1)}},
    )

    assert r.status_code == 422


def test_state_is_scoped_to_the_owner(client, db_session):
    _make_owner(db_session)
    _make_owner(db_session, uid="owner-b")
    client.patch("/users/me/tour-state", json={"tours_seen": {"first-run": "2026-08-24T10:00:00"}})

    other = json.loads(db_session.get(Owner, "owner-b").tour_state or "{}")
    assert other.get("tours_seen", {}) == {}

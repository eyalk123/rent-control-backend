"""Tests for recorded acceptance of the Terms of Service / Privacy Policy.

The behaviour worth protecting: the table is *evidence*. Two things follow, and both are
easy to "tidy up" into being wrong later. Accepting a revised document must not overwrite
the record of what was accepted before it, and re-accepting the same version must not be
deduplicated away — an affirmation is an event, not a flag.
"""

from app.models.device_token import DevicePlatformEnum
from app.models.legal_acceptance import LegalAcceptance, LegalDocumentEnum
from app.models.owner import Owner
from app.repositories.legal_acceptance_repository import LegalAcceptanceRepository
from tests.conftest import OWNER_A, OWNER_B

V1 = "2026-06-09"
V2 = "2026-11-01"


def _make_owner(db_session, uid=OWNER_A):
    owner = Owner(id=uid, email="a@example.com", display_name="Alice")
    db_session.add(owner)
    db_session.commit()
    return owner


def _accept(repo, owner_id=OWNER_A, document=LegalDocumentEnum.TERMS, version=V1, locale="en"):
    return repo.record(
        owner_id=owner_id,
        document=document,
        version=version,
        locale=locale,
        platform=DevicePlatformEnum.WEB,
    )


def _accept_both(client, version=V1, locale="en", platform="web"):
    return client.post(
        "/users/me/legal",
        json={
            "platform": platform,
            "acceptances": [
                {"document": "terms", "version": version, "locale": locale},
                {"document": "privacy", "version": version, "locale": locale},
            ],
        },
    )


# --- Repository ---------------------------------------------------------------

def test_records_what_the_client_displayed(db_session):
    _make_owner(db_session)
    row = _accept(LegalAcceptanceRepository(db_session), version=V1, locale="he")

    assert row.version == V1
    assert row.locale == "he", "which of the two texts they read is part of the record"
    assert row.accepted_at is not None


def test_owner_who_never_accepted_reports_nothing(db_session):
    _make_owner(db_session)
    assert LegalAcceptanceRepository(db_session).latest_per_document(OWNER_A) == {}


def test_unknown_owner_reports_nothing_rather_than_failing(db_session):
    # Asked on first launch, before the profile row exists. Must not 500.
    assert LegalAcceptanceRepository(db_session).latest_per_document("nobody") == {}


def test_re_accepting_does_not_overwrite_the_superseded_record(db_session):
    _make_owner(db_session)
    repo = LegalAcceptanceRepository(db_session)

    _accept(repo, version=V1)
    _accept(repo, version=V2)

    rows = db_session.query(LegalAcceptance).filter_by(owner_id=OWNER_A).all()
    assert {r.version for r in rows} == {V1, V2}, "the old acceptance is evidence; keep it"
    assert repo.latest_per_document(OWNER_A)[LegalDocumentEnum.TERMS].version == V2


def test_repeat_of_the_same_version_is_kept_not_deduplicated(db_session):
    _make_owner(db_session)
    repo = LegalAcceptanceRepository(db_session)

    _accept(repo, version=V1)
    _accept(repo, version=V1)

    assert db_session.query(LegalAcceptance).filter_by(owner_id=OWNER_A).count() == 2


def test_the_two_documents_are_tracked_apart(db_session):
    _make_owner(db_session)
    repo = LegalAcceptanceRepository(db_session)

    _accept(repo, document=LegalDocumentEnum.TERMS, version=V2)
    _accept(repo, document=LegalDocumentEnum.PRIVACY, version=V1)

    latest = repo.latest_per_document(OWNER_A)
    assert latest[LegalDocumentEnum.TERMS].version == V2
    assert latest[LegalDocumentEnum.PRIVACY].version == V1


def test_delete_owner_data_removes_only_that_owner(db_session):
    _make_owner(db_session)
    _make_owner(db_session, OWNER_B)
    repo = LegalAcceptanceRepository(db_session)
    _accept(repo, owner_id=OWNER_A)
    _accept(repo, owner_id=OWNER_B)

    repo.delete_owner_data(OWNER_A)
    db_session.commit()

    assert repo.latest_per_document(OWNER_A) == {}
    assert repo.latest_per_document(OWNER_B) != {}


# --- Endpoints ----------------------------------------------------------------

def test_get_reports_nothing_accepted_for_a_new_account(client, db_session):
    _make_owner(db_session)
    r = client.get("/users/me/legal")

    assert r.status_code == 200
    body = r.json()
    assert body["terms"] is None
    assert body["privacy"] is None
    assert body["required_terms_version"]
    assert body["required_privacy_version"]


def test_post_records_both_documents_in_one_request(client, db_session):
    _make_owner(db_session)
    r = _accept_both(client, version=V1, locale="he")

    assert r.status_code == 201
    body = r.json()
    assert body["terms"]["version"] == V1
    assert body["privacy"]["version"] == V1
    assert body["terms"]["locale"] == "he"
    assert body["terms"]["platform"] == "web"


def test_post_accepts_one_document_without_touching_the_other(client, db_session):
    """A revision to one document must not silently renew the record for the other."""
    _make_owner(db_session)
    _accept_both(client, version=V1)

    client.post(
        "/users/me/legal",
        json={
            "platform": "ios",
            "acceptances": [{"document": "terms", "version": V2, "locale": "en"}],
        },
    )
    body = client.get("/users/me/legal").json()

    assert body["terms"]["version"] == V2
    assert body["privacy"]["version"] == V1


def test_get_reflects_the_latest_acceptance(client, db_session):
    _make_owner(db_session)
    _accept_both(client, version=V1)
    _accept_both(client, version=V2)

    assert client.get("/users/me/legal").json()["terms"]["version"] == V2


def test_post_rejects_an_unknown_document(client, db_session):
    _make_owner(db_session)
    r = client.post(
        "/users/me/legal",
        json={
            "platform": "web",
            "acceptances": [{"document": "cookies", "version": V1, "locale": "en"}],
        },
    )
    assert r.status_code == 422


def test_post_rejects_an_empty_payload(client, db_session):
    _make_owner(db_session)
    r = client.post("/users/me/legal", json={"platform": "web", "acceptances": []})
    assert r.status_code == 422


def test_acceptances_are_scoped_to_the_owner(client_factory, db_session):
    _make_owner(db_session, OWNER_A)
    _make_owner(db_session, OWNER_B)

    _accept_both(client_factory(OWNER_A), version=V1)
    body = client_factory(OWNER_B).get("/users/me/legal").json()

    assert body["terms"] is None, "one owner must not see another's acceptance"

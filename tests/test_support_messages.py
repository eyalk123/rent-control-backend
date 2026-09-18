"""POST /support-messages — the in-app "report a bug" form.

The test that matters most is ``test_body_carries_nothing_internal``. The whole
design rests on one property of mail clients: replying quotes the body and drops
attachments. If an owner id ever migrates from ``diagnostics.txt`` into the body,
the next routine reply pastes it into a customer's mailbox, and nothing else in
the system would notice.
"""
import base64
from datetime import datetime

import pytest
import requests

from app.api.dependencies import get_current_user
from app.config import settings
from app.main import app
from app.models.owner import Owner
from app.models.support_message import SupportMessage
from app.repositories.support_message_repository import SupportMessageRepository
from app.services.user_service import UserService
from tests.conftest import OWNER_A

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 64).decode("ascii")
MESSAGE = "The CPI row shows the wrong index month."


@pytest.fixture
def owner_row(client, db_session):
    """A seeded owner, plus the token claims a real request would carry.

    Depends on ``client`` so it runs after the shared override is installed, and
    re-installs one with ``email``/``name``: every authenticated router carries
    ``get_current_owner``, which refreshes the profile row from the claims on the
    way in. With the suite's claim-less default the upsert would blank the very
    email this feature replies to.
    """
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": OWNER_A,
        "role": "owner",
        "email": "dani@example.com",
        "name": "Dani Cohen",
    }
    owner = Owner(
        id=OWNER_A,
        email="dani@example.com",
        display_name="Dani Cohen",
        language="he",
        country="IL",
    )
    db_session.add(owner)
    db_session.commit()
    return owner


@pytest.fixture
def sent(monkeypatch):
    """Capture the Resend payload instead of posting it."""
    calls = []

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "resend-1"}

    def _fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return _Response()

    monkeypatch.setattr("app.services.support_message_service.requests.post", _fake_post)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(settings, "RESEND_FROM_ADDRESS", "onboarding@resend.dev")
    monkeypatch.setattr(settings, "SUPPORT_EMAIL_TO", "owner@example.com")
    return calls


def _payload(**overrides):
    body = {"type": "bug", "message": MESSAGE}
    body.update(overrides)
    return body


def _attachment(call, filename):
    return next(a for a in call["json"]["attachments"] if a["filename"] == filename)


def _diagnostics(call):
    return base64.b64decode(_attachment(call, "diagnostics.txt")["content"]).decode()


# -- the happy path --------------------------------------------------------

def test_submit_stores_row_and_sends(client, db_session, owner_row, sent):
    resp = client.post("/support-messages", json=_payload())

    assert resp.status_code == 201
    assert resp.json()["type"] == "bug"

    rows = db_session.query(SupportMessage).all()
    assert len(rows) == 1
    assert rows[0].message == MESSAGE
    assert rows[0].email_sent_at is not None
    assert len(sent) == 1


def test_reply_to_is_the_submitter(client, owner_row, sent):
    client.post("/support-messages", json=_payload())

    assert sent[0]["json"]["reply_to"] == "dani@example.com"
    # Their address must never be the From:, which would fail SPF.
    assert sent[0]["json"]["from"] == "onboarding@resend.dev"
    assert sent[0]["json"]["to"] == ["owner@example.com"]


def test_body_carries_nothing_internal(client, owner_row, sent):
    """The body is quoted back to the user on reply. Only their own words belong."""
    client.post("/support-messages", json=_payload())

    text = sent[0]["json"]["text"]
    assert MESSAGE in text
    assert "Dani Cohen" in text
    assert OWNER_A not in text
    assert "dani@example.com" not in text


def test_diagnostics_ride_in_an_attachment(client, owner_row, sent):
    client.post("/support-messages", json=_payload())

    raw = _diagnostics(sent[0])
    assert OWNER_A in raw
    assert "dani@example.com" in raw
    assert "IL" in raw


def test_client_headers_become_diagnostics(client, owner_row, sent):
    client.post(
        "/support-messages",
        json=_payload(),
        headers={
            "X-Client-App": "mobile",
            "X-Client-Platform": "ios",
            "X-Client-Version": "1.4.2",
        },
    )

    raw = _diagnostics(sent[0])
    assert "mobile" in raw and "ios" in raw and "1.4.2" in raw


def test_screenshots_are_attachments(client, db_session, owner_row, sent):
    resp = client.post(
        "/support-messages",
        json=_payload(
            screenshots=[
                {"filename": "shot.png", "content_type": "image/png", "data": PNG},
                {"filename": "two.png", "content_type": "image/png", "data": PNG},
            ]
        ),
    )

    assert resp.status_code == 201
    names = [a["filename"] for a in sent[0]["json"]["attachments"]]
    assert names == ["diagnostics.txt", "shot.png", "two.png"]
    # Counted, never stored.
    row = db_session.query(SupportMessage).one()
    assert row.screenshot_count == 2
    assert not hasattr(row, "screenshot_urls")


def test_screenshot_filename_is_stripped_of_paths(client, owner_row, sent):
    client.post(
        "/support-messages",
        json=_payload(
            screenshots=[
                {
                    "filename": "../../etc/passwd.png",
                    "content_type": "image/png",
                    "data": PNG,
                }
            ]
        ),
    )

    assert [a["filename"] for a in sent[0]["json"]["attachments"]][1] == "passwd.png"


# -- failure is reported, not swallowed ------------------------------------

def test_send_failure_answers_502_but_keeps_the_row(
    client, db_session, owner_row, monkeypatch
):
    def _boom(*args, **kwargs):
        raise requests.ConnectionError("resend unreachable")

    monkeypatch.setattr("app.services.support_message_service.requests.post", _boom)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(settings, "SUPPORT_EMAIL_TO", "owner@example.com")

    resp = client.post("/support-messages", json=_payload())

    assert resp.status_code == 502
    row = db_session.query(SupportMessage).one()
    assert row.message == MESSAGE
    assert row.email_sent_at is None


def test_unconfigured_resend_answers_502(client, db_session, owner_row, monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")

    resp = client.post("/support-messages", json=_payload())

    assert resp.status_code == 502
    assert db_session.query(SupportMessage).one().email_sent_at is None


# -- guards ----------------------------------------------------------------

def test_hourly_limit(client, owner_row, sent, monkeypatch):
    monkeypatch.setattr(settings, "SUPPORT_MESSAGE_HOURLY_LIMIT", 2)

    assert client.post("/support-messages", json=_payload()).status_code == 201
    assert client.post("/support-messages", json=_payload()).status_code == 201
    third = client.post("/support-messages", json=_payload())

    assert third.status_code == 429
    assert third.headers["Retry-After"] == "3600"
    assert len(sent) == 2


@pytest.mark.parametrize(
    "screenshots",
    [
        pytest.param(
            [
                {"filename": f"{i}.png", "content_type": "image/png", "data": PNG}
                for i in range(4)
            ],
            id="four-screenshots",
        ),
        pytest.param(
            [{"filename": "a.pdf", "content_type": "application/pdf", "data": PNG}],
            id="not-an-image",
        ),
        pytest.param(
            [{"filename": "a.png", "content_type": "image/png", "data": "not base64!!"}],
            id="undecodable",
        ),
    ],
)
def test_rejected_screenshots(client, db_session, owner_row, sent, screenshots):
    resp = client.post("/support-messages", json=_payload(screenshots=screenshots))

    assert resp.status_code == 422
    assert db_session.query(SupportMessage).count() == 0


def test_oversized_screenshots_rejected(client, owner_row, sent, monkeypatch):
    monkeypatch.setattr(settings, "SUPPORT_MESSAGE_MAX_ATTACHMENT_BYTES", 10)

    resp = client.post(
        "/support-messages",
        json=_payload(
            screenshots=[{"filename": "a.png", "content_type": "image/png", "data": PNG}]
        ),
    )

    assert resp.status_code == 422


def test_empty_message_rejected(client, owner_row, sent):
    assert client.post("/support-messages", json=_payload(message="")).status_code == 422


def test_unknown_type_rejected(client, owner_row, sent):
    resp = client.post("/support-messages", json=_payload(type="complaint"))
    assert resp.status_code == 422


# -- the section 14 promise ------------------------------------------------

def test_support_messages_die_with_the_account(client, db_session, owner_row, sent):
    client.post("/support-messages", json=_payload())
    assert (
        SupportMessageRepository(db_session).count_since(OWNER_A, datetime(1970, 1, 1))
        == 1
    )

    UserService(db_session).delete_account(OWNER_A)

    assert db_session.query(SupportMessage).count() == 0

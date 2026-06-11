"""Tests for device-token registration and the daily reminder push job."""
from datetime import date, timedelta

import pytest
from freezegun import freeze_time

from app.config import settings
from app.repositories.device_token_repository import DeviceTokenRepository
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_property, make_renter


CRON_SECRET = "test-secret"


# --- device tokens -----------------------------------------------------------

def test_register_device_token(client, db_session):
    resp = client.post("/device-tokens", json={"token": "ExponentPushToken[a]", "platform": "ios"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["token"] == "ExponentPushToken[a]"
    assert body["platform"] == "ios"
    tokens = DeviceTokenRepository(db_session).list_by_owner(OWNER_A)
    assert [t.token for t in tokens] == ["ExponentPushToken[a]"]


def test_register_device_token_stores_locale(client, db_session):
    resp = client.post(
        "/device-tokens",
        json={"token": "ExponentPushToken[a]", "platform": "ios", "locale": "he"},
    )
    assert resp.status_code == 201
    assert resp.json()["locale"] == "he"
    tokens = DeviceTokenRepository(db_session).list_by_owner(OWNER_A)
    assert tokens[0].locale == "he"


def test_register_device_token_is_idempotent(client, db_session):
    client.post(
        "/device-tokens",
        json={"token": "ExponentPushToken[a]", "platform": "ios", "locale": "en"},
    )
    client.post(
        "/device-tokens",
        json={"token": "ExponentPushToken[a]", "platform": "android", "locale": "he"},
    )
    tokens = DeviceTokenRepository(db_session).list_by_owner(OWNER_A)
    assert len(tokens) == 1
    assert tokens[0].platform.value == "android"  # re-register updates in place
    assert tokens[0].locale == "he"  # ...including a language change


def test_unregister_device_token(client, db_session):
    client.post("/device-tokens", json={"token": "ExponentPushToken[a]", "platform": "ios"})
    resp = client.delete("/device-tokens", params={"token": "ExponentPushToken[a]"})
    assert resp.status_code == 204
    assert DeviceTokenRepository(db_session).list_by_owner(OWNER_A) == []


def test_cannot_unregister_other_owners_token(client_factory, db_session):
    client_b = client_factory(OWNER_B)
    client_b.post("/device-tokens", json={"token": "ExponentPushToken[b]", "platform": "ios"})
    client_a = client_factory(OWNER_A)
    resp = client_a.delete("/device-tokens", params={"token": "ExponentPushToken[b]"})
    assert resp.status_code == 204  # idempotent no-op
    # Owner B's token is untouched.
    assert len(DeviceTokenRepository(db_session).list_by_owner(OWNER_B)) == 1


# --- reminder job ------------------------------------------------------------

@pytest.fixture
def captured_pushes(monkeypatch):
    """Mock the Expo HTTP call; record every message sent and return ok tickets."""
    sent: list[dict] = []

    class _FakeResponse:
        def __init__(self, messages):
            self._messages = messages

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"status": "ok", "id": "ticket"} for _ in self._messages]}

    def _fake_post(url, json=None, headers=None, timeout=None):
        sent.extend(json or [])
        return _FakeResponse(json or [])

    monkeypatch.setattr("app.services.push_service.requests.post", _fake_post)
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)
    return sent


def _seed_overdue_and_expiring(db_session, today):
    prop = make_property(db_session)
    overdue = make_renter(
        db_session,
        property_id=prop.id,
        first_name="Late",
        lease_start=today - timedelta(days=100),
        lease_end=today + timedelta(days=200),
        payment_day_of_month=1,
    )
    expiring = make_renter(
        db_session,
        property_id=prop.id,
        first_name="Soon",
        lease_start=today - timedelta(days=300),
        lease_end=today + timedelta(days=20),  # within default 30-day window
    )
    return overdue, expiring


@freeze_time("2026-06-15")
def test_run_reminders_sends_overdue_and_expiring(client, db_session, captured_pushes):
    today = date(2026, 6, 15)
    client.post("/device-tokens", json={"token": "ExponentPushToken[a]", "platform": "ios"})
    _seed_overdue_and_expiring(db_session, today)

    resp = client.post("/internal/run-reminders", headers={"X-Cron-Secret": CRON_SECRET})
    assert resp.status_code == 200
    assert resp.json()["sent"] == {"overdue": 1, "lease_expiring": 1}
    types = sorted(m["data"]["type"] for m in captured_pushes)
    assert types == ["lease_expiring", "overdue"]


@freeze_time("2026-06-15")
def test_run_reminders_localizes_per_device(client, db_session, captured_pushes):
    """Two devices on the same owner in different languages each get their own copy;
    a device with no stored locale falls back to English."""
    today = date(2026, 6, 15)
    client.post(
        "/device-tokens",
        json={"token": "ExponentPushToken[he]", "platform": "ios", "locale": "he"},
    )
    client.post(
        "/device-tokens",
        json={"token": "ExponentPushToken[en]", "platform": "android", "locale": "en"},
    )
    client.post("/device-tokens", json={"token": "ExponentPushToken[none]", "platform": "web"})
    _seed_overdue_and_expiring(db_session, today)

    resp = client.post("/internal/run-reminders", headers={"X-Cron-Secret": CRON_SECRET})
    assert resp.status_code == 200

    overdue = {m["to"]: m for m in captured_pushes if m["data"]["type"] == "overdue"}
    assert overdue["ExponentPushToken[he]"]["title"] == "שכר דירה באיחור"
    assert overdue["ExponentPushToken[en]"]["title"] == "Rent overdue"
    assert overdue["ExponentPushToken[none]"]["title"] == "Rent overdue"  # fallback
    assert "באיחור" in overdue["ExponentPushToken[he]"]["body"]
    assert overdue["ExponentPushToken[en]"]["body"].startswith("Rent from ")


@freeze_time("2026-06-15")
def test_run_reminders_deduplicates_same_day(client, db_session, captured_pushes):
    today = date(2026, 6, 15)
    client.post("/device-tokens", json={"token": "ExponentPushToken[a]", "platform": "ios"})
    _seed_overdue_and_expiring(db_session, today)

    first = client.post("/internal/run-reminders", headers={"X-Cron-Secret": CRON_SECRET})
    assert first.json()["sent"] == {"overdue": 1, "lease_expiring": 1}
    captured_pushes.clear()

    second = client.post("/internal/run-reminders", headers={"X-Cron-Secret": CRON_SECRET})
    assert second.json()["sent"] == {"overdue": 0, "lease_expiring": 0}
    assert captured_pushes == []


@freeze_time("2026-06-15")
def test_run_reminders_skips_owners_without_devices(client, db_session, captured_pushes):
    today = date(2026, 6, 15)
    _seed_overdue_and_expiring(db_session, today)  # no device token registered

    resp = client.post("/internal/run-reminders", headers={"X-Cron-Secret": CRON_SECRET})
    assert resp.json()["sent"] == {"overdue": 0, "lease_expiring": 0}
    assert captured_pushes == []


def test_run_reminders_rejects_bad_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)
    assert client.post("/internal/run-reminders", headers={"X-Cron-Secret": "wrong"}).status_code == 401
    assert client.post("/internal/run-reminders").status_code == 401

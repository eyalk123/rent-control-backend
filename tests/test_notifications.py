"""Tests for device-token registration and the daily reminder push job."""
from datetime import date, timedelta

import pytest
from freezegun import freeze_time

from app.config import settings
from app.models.notification_settings import NotificationSettings
from app.repositories.device_token_repository import DeviceTokenRepository
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_property, make_renter, make_transaction


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

class _Captured(list):
    """A list of all messages sent, plus a count of HTTP POSTs made to Expo."""
    post_calls = 0


@pytest.fixture
def captured_pushes(monkeypatch):
    """Mock the Expo HTTP call; record every message sent and return ok tickets."""
    sent = _Captured()

    class _FakeResponse:
        def __init__(self, messages):
            self._messages = messages

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"status": "ok", "id": "ticket"} for _ in self._messages]}

    def _fake_post(url, json=None, headers=None, timeout=None):
        sent.post_calls += 1
        sent.extend(json or [])
        return _FakeResponse(json or [])

    monkeypatch.setattr("app.services.push_service.requests.post", _fake_post)
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)
    return sent


def _seed_overdue_and_expiring(db_session, today):
    """Seed exactly one overdue and one expiring renter that each match a single
    default offset (overdue 1 day -> offset 0; lease 40 days out -> offset 90)."""
    prop = make_property(db_session)
    overdue = make_renter(
        db_session,
        property_id=prop.id,
        first_name="Late",
        lease_start=today - timedelta(days=100),
        lease_end=today + timedelta(days=200),
        payment_day_of_month=14,  # today is the 15th -> 1 day overdue
    )
    expiring = make_renter(
        db_session,
        property_id=prop.id,
        first_name="Soon",
        lease_start=today - timedelta(days=300),
        lease_end=today + timedelta(days=40),  # 40 days out -> default offset 90 only
        payment_day_of_month=28,  # not yet due -> isolates the lease_expiring case
    )
    return overdue, expiring


@freeze_time("2026-06-15")
def test_run_reminders_sends_overdue_and_expiring(client, db_session, captured_pushes):
    today = date(2026, 6, 15)
    client.post("/device-tokens", json={"token": "ExponentPushToken[a]", "platform": "ios"})
    _seed_overdue_and_expiring(db_session, today)

    resp = client.post("/internal/run-reminders", headers={"X-Cron-Secret": CRON_SECRET})
    assert resp.status_code == 200
    assert resp.json()["sent"] == {"created": 2, "pushed": 2}
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
def test_run_reminders_batches_into_one_post(client, db_session, captured_pushes):
    """Many overdue renters are sent in a single Expo POST, not one per renter."""
    today = date(2026, 6, 15)
    client.post("/device-tokens", json={"token": "ExponentPushToken[a]", "platform": "ios"})
    prop = make_property(db_session)
    for i in range(5):
        make_renter(
            db_session,
            property_id=prop.id,
            first_name=f"Late{i}",
            lease_start=today - timedelta(days=100),
            lease_end=today + timedelta(days=200),
            payment_day_of_month=14,  # 1 day overdue -> a single default offset each
        )

    resp = client.post("/internal/run-reminders", headers={"X-Cron-Secret": CRON_SECRET})
    assert resp.json()["sent"]["created"] == 5
    assert len(captured_pushes) == 5  # one message per renter
    assert captured_pushes.post_calls == 1  # ...all in a single HTTP request


@freeze_time("2026-06-15")
def test_run_reminders_deduplicates_same_day(client, db_session, captured_pushes):
    today = date(2026, 6, 15)
    client.post("/device-tokens", json={"token": "ExponentPushToken[a]", "platform": "ios"})
    _seed_overdue_and_expiring(db_session, today)

    first = client.post("/internal/run-reminders", headers={"X-Cron-Secret": CRON_SECRET})
    assert first.json()["sent"] == {"created": 2, "pushed": 2}
    captured_pushes.clear()

    second = client.post("/internal/run-reminders", headers={"X-Cron-Secret": CRON_SECRET})
    assert second.json()["sent"] == {"created": 0, "pushed": 0}
    assert captured_pushes == []


@freeze_time("2026-06-15")
def test_run_reminders_writes_feed_but_skips_push_without_devices(client, db_session, captured_pushes):
    """No device -> the feed is still written (web reads it); only push is skipped."""
    today = date(2026, 6, 15)
    _seed_overdue_and_expiring(db_session, today)  # no device token registered

    resp = client.post("/internal/run-reminders", headers={"X-Cron-Secret": CRON_SECRET})
    assert resp.json()["sent"] == {"created": 2, "pushed": 0}
    assert captured_pushes == []


@freeze_time("2026-06-15")
def test_run_reminders_pushes_rows_created_by_feed(client, db_session, captured_pushes):
    """Regression: opening the app materializes feed rows without pushing. The cron must
    still push those rows even though it didn't create them this run."""
    today = date(2026, 6, 15)
    _seed_overdue_and_expiring(db_session, today)

    # Simulate the user opening the app: the feed persists rows but never pushes.
    feed = client.get("/notifications")
    assert feed.status_code == 200
    assert captured_pushes == []

    client.post("/device-tokens", json={"token": "ExponentPushToken[a]", "platform": "ios"})
    resp = client.post("/internal/run-reminders", headers={"X-Cron-Secret": CRON_SECRET})
    # created=0 (the feed already made them) but they're still pushed now.
    assert resp.json()["sent"] == {"created": 0, "pushed": 2}
    assert sorted(m["data"]["type"] for m in captured_pushes) == ["lease_expiring", "overdue"]


def test_run_reminders_suppresses_stale_unpushed(client, db_session, captured_pushes):
    """Un-pushed rows older than the freshness window are marked pushed, not sent late."""
    from app.repositories.notification_repository import NotificationRepository
    from datetime import datetime

    client.post("/device-tokens", json={"token": "ExponentPushToken[a]", "platform": "ios"})

    # The feed materializes rows on the 20th; no cron pushes them then.
    with freeze_time("2026-06-20"):
        _seed_overdue_and_expiring(db_session, date(2026, 6, 20))
        client.get("/notifications")

    # The cron only runs five days later — past the freshness window.
    with freeze_time("2026-06-25"):
        resp = client.post("/internal/run-reminders", headers={"X-Cron-Secret": CRON_SECRET})

    assert resp.json()["sent"]["pushed"] == 0
    assert captured_pushes == []
    # The stale rows are marked pushed so they won't resurface on a later run.
    leftover = NotificationRepository(db_session).list_unpushed_for_owner(
        OWNER_A, datetime(2000, 1, 1)
    )
    assert leftover == []


def test_run_reminders_rejects_bad_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)
    assert client.post("/internal/run-reminders", headers={"X-Cron-Secret": "wrong"}).status_code == 401
    assert client.post("/internal/run-reminders").status_code == 401


# --- feed resolve-on-read (auto-dismiss when the condition clears) ------------

def _types(feed_response) -> list[str]:
    return [n["type"] for n in feed_response.json()]


@freeze_time("2026-06-15")
def test_feed_clears_overdue_after_payment(client, db_session):
    """Recording the rent (a revenue txn for this month) drops the renter out of
    'overdue', so the next feed read auto-dismisses the stale alert."""
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        lease_start=date(2026, 3, 1),
        lease_end=date(2026, 12, 31),
        payment_day_of_month=14,  # today is the 15th -> 1 day overdue
    )

    assert _types(client.get("/notifications")) == ["overdue"]

    # Rent gets paid for the current month, outside the alert's own pill.
    make_transaction(
        db_session,
        property_id=prop.id,
        renter_id=renter.id,
        month_for=date(2026, 6, 1),
    )

    assert _types(client.get("/notifications")) == []


@freeze_time("2026-06-15")
def test_feed_clears_lease_expiring_after_extension(client, db_session):
    """Extending the lease past the reminder window leaves the old lease_expiring
    row with no backing candidate; the next feed read clears it."""
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        lease_start=date(2025, 6, 1),
        lease_end=date(2026, 6, 15) + timedelta(days=40),  # 40 days out -> offset 90
        payment_day_of_month=28,  # not yet due -> isolates lease_expiring
    )

    assert _types(client.get("/notifications")) == ["lease_expiring"]

    # Lease extended well beyond the 90-day window.
    renter.lease_end = date(2026, 6, 15) + timedelta(days=400)
    db_session.commit()

    assert _types(client.get("/notifications")) == []


@freeze_time("2026-06-15")
def test_feed_keeps_unresolved_alert(client, db_session):
    """A still-overdue renter keeps producing the same candidate, so the row is
    preserved across reads (guards against over-aggressive dismissal)."""
    prop = make_property(db_session)
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=date(2026, 3, 1),
        lease_end=date(2026, 12, 31),
        payment_day_of_month=14,
    )

    assert _types(client.get("/notifications")) == ["overdue"]
    assert _types(client.get("/notifications")) == ["overdue"]


@freeze_time("2026-06-15")
def test_feed_does_not_dismiss_when_notifications_disabled(client, db_session):
    """Account-wide disable yields zero candidates; reconciliation must not read
    that as 'everything resolved' and wipe existing rows."""
    prop = make_property(db_session)
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=date(2026, 3, 1),
        lease_end=date(2026, 12, 31),
        payment_day_of_month=14,
    )

    assert _types(client.get("/notifications")) == ["overdue"]

    db_session.add(NotificationSettings(owner_id=OWNER_A, master_enabled=False))
    db_session.commit()

    # Still surfaced (the feed itself ignores settings); crucially, not dismissed.
    assert _types(client.get("/notifications")) == ["overdue"]


def test_feed_refreshes_overdue_days_across_reads(client, db_session):
    """The stored days_overdue is frozen at row creation; the feed must overwrite
    it with the current value on each read so the count tracks reality."""
    prop = make_property(db_session)
    make_renter(
        db_session, property_id=prop.id,
        lease_start=date(2026, 1, 1), lease_end=date(2026, 12, 31),
        payment_day_of_month=1,  # due on the 1st
    )

    def days_shown():
        feed = client.get("/notifications").json()
        overdue = [n for n in feed if n["type"] == "overdue"]
        return overdue[0]["data"]["days_overdue"] if overdue else None

    with freeze_time("2026-06-15"):
        assert days_shown() == 14  # first materialization
    with freeze_time("2026-06-25"):
        assert days_shown() == 24  # refreshed (was frozen at 14 before the fix)


def test_feed_refreshes_lease_days_across_reads(client, db_session):
    """Same for lease_expiring: days_until_expiry tracks the current value, which
    also keeps the client-reconstructed lease-end date correct."""
    prop = make_property(db_session)
    make_renter(
        db_session, property_id=prop.id,
        lease_start=date(2025, 1, 1), lease_end=date(2026, 9, 1),
        payment_day_of_month=28,  # not yet due -> isolates lease_expiring
    )

    def days_shown():
        feed = client.get("/notifications").json()
        exp = [n for n in feed if n["type"] == "lease_expiring"]
        return exp[0]["data"]["days_until_expiry"] if exp else None

    with freeze_time("2026-06-15"):
        assert days_shown() == 78  # only offset 90 in play; frozen here before fix
    with freeze_time("2026-06-25"):
        assert days_shown() == 68  # refreshed to the current value


@freeze_time("2026-06-15")
def test_feed_does_not_dismiss_muted_event(client, db_session):
    """A muted event also produces no candidates; its existing rows stay put
    rather than being treated as resolved."""
    prop = make_property(db_session)
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=date(2026, 3, 1),
        lease_end=date(2026, 12, 31),
        payment_day_of_month=14,
    )

    assert _types(client.get("/notifications")) == ["overdue"]

    db_session.add(NotificationSettings(owner_id=OWNER_A, muted_events='["overdue"]'))
    db_session.commit()

    assert _types(client.get("/notifications")) == ["overdue"]

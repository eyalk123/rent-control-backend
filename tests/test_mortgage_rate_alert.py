"""The mortgage rate-change alert: the Bank of Israel rate is kept as its changes, and when
it moves, owners with a prime-track mortgage get one alert — not one per mortgage."""
from datetime import date, datetime

import pytest
from freezegun import freeze_time

from app.config import settings
from app.models.notification import Notification, NotificationTypeEnum
from app.models.notification_settings import NotificationSettings
from app.models.policy_rate import PolicyRate
from app.models.property_mortgage import MortgagePayment, PropertyMortgage
from app.repositories.policy_rate_repository import PolicyRateRepository
from app.services import policy_rate_service
from app.services.policy_rate_service import PolicyRateService, _changes
from tests.conftest import OWNER_A
from tests.factories import make_property

CRON_SECRET = "test-secret"
RUN = {"X-Cron-Secret": CRON_SECRET}
# The rate went from 3.5 to 3.25 on 3 Sep; the job runs two days later.
CHANGE_DAY = date(2026, 9, 3)
TODAY = "2026-09-05"


@pytest.fixture
def pushes(monkeypatch):
    sent: list[dict] = []

    class _Response:
        def __init__(self, messages):
            self._messages = messages

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"status": "ok", "id": "t"} for _ in self._messages]}

    def _post(url, json=None, headers=None, timeout=None):
        sent.extend(json or [])
        return _Response(json or [])

    monkeypatch.setattr("app.services.push_service.requests.post", _post)
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)
    # The job refreshes the rate when someone has a prime mortgage; never over the network.
    monkeypatch.setattr(PolicyRateService, "_fetch", lambda self, start: [])
    return sent


def _rates(db_session, *changes):
    PolicyRateRepository(db_session).add_all("IL", list(changes))


def _mortgage(db_session, prop, prime=True, created=datetime(2026, 1, 1), end_date=None):
    m = PropertyMortgage(
        property_id=prop.id,
        charge_day=10,
        has_prime_track=prime,
        end_date=end_date,
        created_at=created,
    )
    m.payments.append(MortgagePayment(amount=5000, effective_from=date(2026, 1, 1)))
    db_session.add(m)
    db_session.commit()
    return m


def _device(client, locale="en"):
    client.post(
        "/device-tokens",
        json={"token": f"ExponentPushToken[{locale}]", "platform": "ios", "locale": locale},
    )


# --- the rate ------------------------------------------------------------------------------


def test_changes_keeps_only_the_days_the_rate_moved():
    daily = [
        (date(2026, 7, 8), 3.5),
        (date(2026, 7, 9), 3.5),
        (date(2026, 9, 2), 3.5),
        (date(2026, 9, 3), 3.25),
        (date(2026, 9, 4), 3.25),
    ]
    assert _changes(daily, None, None) == [(date(2026, 7, 8), 3.5), (date(2026, 9, 3), 3.25)]
    # Against what is stored: nothing before it, and no repeat of it.
    assert _changes(daily, 3.5, date(2026, 7, 9)) == [(date(2026, 9, 3), 3.25)]
    assert _changes(daily, 3.25, date(2026, 9, 3)) == []


def test_refresh_backfills_then_adds_only_new_changes(db_session, monkeypatch):
    service = PolicyRateService(PolicyRateRepository(db_session), "http://x", "S")
    feed = [(date(2026, 7, 9), 3.5), (date(2026, 9, 3), 3.25)]
    monkeypatch.setattr(PolicyRateService, "_fetch", lambda self, start: list(feed))

    assert service.refresh(today=date(2026, 9, 5))["added"] == 2
    assert service.refresh(today=date(2026, 9, 6))["added"] == 0  # idempotent
    feed.append((date(2026, 10, 15), 3.0))
    assert service.refresh(today=date(2026, 10, 16))["added"] == 1
    assert [r.rate for r in db_session.query(PolicyRate).order_by(PolicyRate.effective_date)] == [
        3.5, 3.25, 3.0,
    ]


def test_refresh_survives_an_unreachable_upstream(db_session, monkeypatch):
    monkeypatch.setattr(policy_rate_service, "fetch_json", lambda *a, **k: None)
    service = PolicyRateService(PolicyRateRepository(db_session), "http://x", "S")
    assert service.refresh() == {"added": 0, "fetched": False}


def test_parse_reads_the_sdmx_response():
    data = {
        "data": {
            "structure": {"dimensions": {"observation": [
                {"id": "TIME_PERIOD", "values": [{"id": "2026-09-02"}, {"id": "2026-09-03"}]}
            ]}},
            "dataSets": [{"series": {"0:0:0": {"observations": {"0": ["3.5"], "1": ["3.25"]}}}}],
        }
    }
    assert policy_rate_service._parse(data) == [(date(2026, 9, 2), 3.5), (date(2026, 9, 3), 3.25)]


# --- the alert -----------------------------------------------------------------------------


@freeze_time(TODAY)
def test_one_alert_for_several_prime_mortgages(client, db_session, pushes):
    _device(client, "he")
    _rates(db_session, (date(2026, 7, 9), 3.5), (CHANGE_DAY, 3.25))
    _mortgage(db_session, make_property(db_session, address="1 Herzl"))
    _mortgage(db_session, make_property(db_session, address="2 Herzl"))

    body = client.post("/internal/run-reminders", headers=RUN).json()
    assert body["sent"]["pushed"] == 1
    [push] = pushes
    assert push["title"] == "ריבית הפריים ירדה"
    assert "4.75%" in push["body"] and "5%" in push["body"]
    assert push["data"] == {"type": "mortgage_rate_change", "route": "/properties"}

    # Only once, however often the job runs.
    client.post("/internal/run-reminders", headers=RUN)
    assert db_session.query(Notification).filter_by(
        type=NotificationTypeEnum.MORTGAGE_RATE_CHANGE
    ).count() == 1


@freeze_time(TODAY)
def test_one_property_opens_it(client, db_session, pushes):
    _device(client)
    _rates(db_session, (date(2026, 7, 9), 3.0), (CHANGE_DAY, 3.25))
    prop = make_property(db_session)
    _mortgage(db_session, prop)

    client.post("/internal/run-reminders", headers=RUN)
    [push] = pushes
    assert push["title"] == "The prime rate went up"
    assert push["data"]["route"] == f"/properties/{prop.id}"


@freeze_time(TODAY)
@pytest.mark.parametrize(
    "mortgage",
    [
        dict(prime=False),  # the owner said there is no prime track
        dict(created=datetime(2026, 9, 4)),  # added after the change, at the new amount
        dict(end_date=date(2026, 8, 1)),  # already paid off
    ],
)
def test_no_alert(client, db_session, pushes, mortgage):
    _device(client)
    _rates(db_session, (date(2026, 7, 9), 3.5), (CHANGE_DAY, 3.25))
    _mortgage(db_session, make_property(db_session), **mortgage)

    client.post("/internal/run-reminders", headers=RUN)
    assert pushes == []


@freeze_time("2026-09-20")
def test_an_old_change_is_not_announced(client, db_session, pushes):
    """The first refresh backfills years of changes; none of them is news."""
    _device(client)
    _rates(db_session, (date(2026, 7, 9), 3.5), (CHANGE_DAY, 3.25))
    _mortgage(db_session, make_property(db_session))

    client.post("/internal/run-reminders", headers=RUN)
    assert pushes == []


@freeze_time(TODAY)
def test_muted_alert_is_not_sent(client, db_session, pushes):
    _device(client)
    db_session.add(NotificationSettings(owner_id=OWNER_A, muted_events='["mortgage_rate_change"]'))
    db_session.commit()
    _rates(db_session, (date(2026, 7, 9), 3.5), (CHANGE_DAY, 3.25))
    _mortgage(db_session, make_property(db_session))

    client.post("/internal/run-reminders", headers=RUN)
    assert pushes == []


@freeze_time(TODAY)
def test_feed_shows_it_only_to_clients_that_ask(client, db_session):
    """Older app builds render an unknown type as an overdue-rent row."""
    _rates(db_session, (date(2026, 7, 9), 3.5), (CHANGE_DAY, 3.25))
    prop = make_property(db_session)
    _mortgage(db_session, prop)

    assert client.get("/notifications").json() == []
    [item] = client.get("/notifications", params={"include_mortgage_alerts": True}).json()
    assert item["type"] == "mortgage_rate_change"
    assert item["renter_id"] == 0
    assert item["property_id"] == prop.id
    assert item["data"]["old_prime"] == 5.0 and item["data"]["new_prime"] == 4.75
    assert item["data"]["effective_date"] == "2026-09-03"


@freeze_time(TODAY)
def test_rate_alert_cannot_have_rules(client):
    r = client.post(
        "/notification-rules",
        json={"event_type": "mortgage_rate_change", "offsets": [0]},
    )
    assert r.status_code == 400

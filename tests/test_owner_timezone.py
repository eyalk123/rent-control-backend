"""Business "today" is the landlord's calendar, read from the timezone their client reports."""
from datetime import date, timedelta

from freezegun import freeze_time

from app.clock import is_valid_timezone, today_in
from app.models.owner import Owner
from app.repositories.owner_repository import owner_today
from tests.conftest import OWNER_A
from tests.factories import make_property, make_renter


def _tz(db_session) -> str | None:
    db_session.expire_all()
    owner = db_session.get(Owner, OWNER_A)
    return owner.timezone if owner else None


def test_reported_timezone_is_stored(client, db_session):
    client.get("/properties", headers={"X-Client-Timezone": "America/New_York"})
    assert _tz(db_session) == "America/New_York"


def test_a_new_timezone_replaces_the_old_one_immediately(client, db_session):
    client.get("/properties", headers={"X-Client-Timezone": "America/New_York"})
    # Inside the last-seen throttle window — the zone must still be written.
    client.get("/properties", headers={"X-Client-Timezone": "Asia/Jerusalem"})
    assert _tz(db_session) == "Asia/Jerusalem"


def test_unknown_or_missing_timezone_never_overwrites(client, db_session):
    client.get("/properties", headers={"X-Client-Timezone": "Asia/Jerusalem"})
    client.get("/properties", headers={"X-Client-Timezone": "Mars/Olympus"})
    client.get("/properties")  # an old app build: no header at all
    assert _tz(db_session) == "Asia/Jerusalem"


def test_is_valid_timezone():
    assert is_valid_timezone("Asia/Jerusalem")
    assert not is_valid_timezone("Mars/Olympus")
    assert not is_valid_timezone("")
    assert not is_valid_timezone(None)


@freeze_time("2026-10-15T01:00:00Z")
def test_today_in_each_zone():
    # 01:00 UTC is still the 14th in New York and already the 15th in Jerusalem.
    assert today_in("America/New_York") == date(2026, 10, 14)
    assert today_in("Asia/Jerusalem") == date(2026, 10, 15)
    assert today_in(None) == date(2026, 10, 15)
    assert today_in("Mars/Olympus") == date(2026, 10, 15)


@freeze_time("2026-10-14T22:00:00Z")
def test_owner_today_reads_the_account_zone(db_session):
    db_session.add(Owner(id=OWNER_A, timezone="Asia/Tokyo"))
    db_session.flush()
    assert owner_today(db_session, OWNER_A) == date(2026, 10, 15)
    assert owner_today(db_session, "no-such-owner") == date(2026, 10, 14)


@freeze_time("2026-06-15T02:00:00Z")
def test_overdue_days_count_on_the_landlords_calendar(client, db_session):
    # 02:00 UTC on the 15th is 22:00 on the 14th in New York.
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        lease_start=date(2026, 6, 15) - timedelta(days=100),
        lease_end=date(2026, 6, 15) + timedelta(days=200),
        payment_day_of_month=1,
    )

    def days_overdue(tz: str) -> int:
        body = client.get("/renters/overdue", headers={"X-Client-Timezone": tz}).json()
        return next(r for r in body if r["renter_id"] == renter.id)["days_overdue"]

    assert days_overdue("Asia/Jerusalem") == 14
    assert days_overdue("America/New_York") == 13

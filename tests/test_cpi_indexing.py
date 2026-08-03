"""Full-stack tests for CPI rent linkage: create materializes amounts server-side,
and the internal cron endpoint refreshes them."""
import json
from datetime import date

from dateutil.relativedelta import relativedelta

from app.api.dependencies import get_index_sources
from app.config import settings
from app.main import app
from app.models.notification import NotificationTypeEnum
from app.repositories.cpi_index_repository import CpiIndexRepository, reference_period
from app.repositories.notification_repository import NotificationRepository
from app.repositories.notification_settings_repository import (
    NotificationSettingsRepository,
)
from tests.conftest import OWNER_A
from tests.factories import make_renter

CRON_SECRET = "cpi-secret"
INDEX_ID = 120010


class FakeSource:
    def __init__(self, name, all_rows=None, latest_rows=None):
        self.name = name
        self._all = all_rows or []
        self._latest = latest_rows or []

    def fetch_all(self):
        return self._all

    def fetch_latest(self, n=6):
        return self._latest


def _seed_index(db_session, periods=None):
    CpiIndexRepository(db_session).upsert_many(
        INDEX_ID, periods or [(2022, 11, 100.0), (2023, 11, 110.0)], source="cbs"
    )


def _fresh_period():
    """A reading for the newest month that should be published — keeps a test off the
    staleness path so it can assert on something else."""
    year, month = reference_period(date.today())
    return (year, month, 110.0)


def test_create_cpi_renter_materializes_amounts(client, db_session):
    _seed_index(db_session)
    payload = {
        "first_name": "Dana",
        "last_name": "Levi",
        "phone": "0501112222",
        "lease_start": "2023-01-01",
        "base_rent": 5000,
        "rent_escalation_mode": "cpi",
        # client sends a flat base-rent projection; the server overwrites amounts.
        "lease_years": [
            {"amount": 5000, "type": "contract"},
            {"amount": 5000, "type": "contract"},
        ],
    }
    resp = client.post("/renters", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["rent_escalation_mode"] == "cpi"
    assert body["cpi_base_index"] == 100.0
    assert [y["amount"] for y in body["lease_years"]] == [5000, 5500]


def _seed_cpi_renter(db_session):
    return make_renter(
        db_session,
        rent_escalation_mode="cpi",
        lease_start=date(2023, 1, 1),
        base_rent=5000.0,
        cpi_base_index=100.0,
        lease_years=[
            {"amount": 5000.0, "type": "contract"},
            {"amount": 5000.0, "type": "contract"},  # stale projection
        ],
    )


def _post(client, sources):
    app.dependency_overrides[get_index_sources] = lambda: sources
    try:
        return client.post(
            "/internal/run-cpi-indexing", headers={"X-Cron-Secret": CRON_SECRET}
        )
    finally:
        app.dependency_overrides.pop(get_index_sources, None)


def test_run_cpi_indexing_endpoint_updates_renters(client, db_session, monkeypatch):
    _seed_index(db_session, [(2022, 11, 100.0), (2023, 11, 110.0), _fresh_period()])
    renter = _seed_cpi_renter(db_session)
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)

    # Wrong / missing secret is rejected.
    assert client.post("/internal/run-cpi-indexing").status_code == 401

    resp = _post(client, [FakeSource("cbs"), FakeSource("boi")])

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["renters_updated"] == 1
    assert body["stale"] is False

    db_session.refresh(renter)
    assert [y["amount"] for y in json.loads(renter.lease_years)] == [5000, 5500]


def test_run_cpi_indexing_endpoint_succeeds_on_the_fallback(client, db_session, monkeypatch):
    """CBS down but BOI serving is a working state — correct rents, so still a 200."""
    renter = _seed_cpi_renter(db_session)
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)

    resp = _post(
        client,
        [
            FakeSource("cbs"),  # unreachable: no rows
            FakeSource("boi", all_rows=[(2022, 11, 100.0), (2023, 11, 110.0), _fresh_period()]),
        ],
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["source"] == "boi"
    assert body["degraded"] is True
    assert body["stale"] is False

    db_session.refresh(renter)
    assert [y["amount"] for y in json.loads(renter.lease_years)] == [5000, 5500]


# --- the rent-change notification -------------------------------------------
#
# The job is the only place the confirmation can be raised: one statement after the
# comparison, the old amount has been overwritten and is gone.


def _seed_repriced_renter(db_session, **kw):
    """A live CPI lease whose *current* year is still holding a stale flat projection,
    so the next run reprices what the tenant owes right now."""
    today = date.today()
    lease_start = today - relativedelta(years=1, months=1)
    return make_renter(
        db_session,
        rent_escalation_mode="cpi",
        lease_start=lease_start,
        lease_end=lease_start + relativedelta(years=3),
        base_rent=5000.0,
        cpi_base_index=100.0,
        lease_years=[{"amount": 5000.0, "type": "contract"} for _ in range(3)],
        **kw,
    )


def _seed_index_for_reprice(db_session):
    today = date.today()
    lease_start = today - relativedelta(years=1, months=1)
    anniversary = lease_start + relativedelta(years=1)
    rows = [
        (*reference_period(lease_start), 100.0),   # the frozen base
        (*reference_period(anniversary), 110.0),   # +10% by the current year's start
        (*reference_period(today), 110.0),         # newest month, so the run isn't stale
    ]
    CpiIndexRepository(db_session).upsert_many(INDEX_ID, rows, source="cbs")
    return anniversary


def _cpi_notifications(db_session, owner_id=OWNER_A):
    return [
        n
        for n in NotificationRepository(db_session).list_for_owner(owner_id)
        if n.type == NotificationTypeEnum.CPI_RENT_CHANGE
    ]


def test_run_cpi_indexing_notifies_when_the_current_year_is_repriced(
    client, db_session, monkeypatch
):
    anniversary = _seed_index_for_reprice(db_session)
    renter = _seed_repriced_renter(db_session)
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)

    resp = _post(client, [FakeSource("cbs"), FakeSource("boi")])

    assert resp.status_code == 200
    assert resp.json()["notified"] == 1

    rows = _cpi_notifications(db_session)
    assert len(rows) == 1
    assert rows[0].entity_id == renter.id
    assert rows[0].offset == 0
    assert rows[0].period_key == anniversary.isoformat()

    data = json.loads(rows[0].data)
    assert data["stage"] == "changed"
    assert data["old_amount"] == 5000.0
    assert data["new_amount"] == 5500
    assert data["delta"] == 500
    assert data["effective_date"] == anniversary.isoformat()
    # Provenance travels with the notification, so a disputed figure is answerable.
    assert data["known_index"] == 110.0
    assert data["index_source"] == "cbs"


def test_run_cpi_indexing_does_not_re_notify_on_a_second_run(
    client, db_session, monkeypatch
):
    _seed_index_for_reprice(db_session)
    _seed_repriced_renter(db_session)
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)

    _post(client, [FakeSource("cbs"), FakeSource("boi")])
    second = _post(client, [FakeSource("cbs"), FakeSource("boi")])

    # The year froze on the first run, so there is nothing left to say.
    assert second.json()["notified"] == 0
    assert second.json()["renters_updated"] == 0
    assert len(_cpi_notifications(db_session)) == 1


def test_run_cpi_indexing_is_silent_for_a_muted_owner(client, db_session, monkeypatch):
    _seed_index_for_reprice(db_session)
    _seed_repriced_renter(db_session)
    NotificationSettingsRepository(db_session).update(
        OWNER_A, {"muted_events": json.dumps(["cpi_rent_change"])}
    )
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)

    resp = _post(client, [FakeSource("cbs"), FakeSource("boi")])

    # Muting silences the alert; it must not stop the rent itself being corrected.
    assert resp.json()["notified"] == 0
    assert resp.json()["renters_updated"] == 1
    assert _cpi_notifications(db_session) == []


def test_run_cpi_indexing_respects_the_materiality_threshold(
    client, db_session, monkeypatch
):
    today = date.today()
    lease_start = today - relativedelta(years=1, months=1)
    anniversary = lease_start + relativedelta(years=1)
    CpiIndexRepository(db_session).upsert_many(
        INDEX_ID,
        [
            (*reference_period(lease_start), 100.0),
            (*reference_period(anniversary), 100.3),  # +₪15, under the ₪25 floor
            (*reference_period(today), 100.3),
        ],
        source="cbs",
    )
    _seed_repriced_renter(db_session)
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)

    resp = _post(client, [FakeSource("cbs"), FakeSource("boi")])

    assert resp.json()["renters_updated"] == 1  # the amount did change...
    assert resp.json()["notified"] == 0         # ...but not by enough to be news
    assert _cpi_notifications(db_session) == []


def test_run_cpi_indexing_is_silent_about_future_years(client, db_session, monkeypatch):
    """A projection firming up for a year that hasn't started changes nothing the
    tenant owes today, so it must not generate an alert."""
    today = date.today()
    lease_start = today - relativedelta(months=1)  # still in year 1
    CpiIndexRepository(db_session).upsert_many(
        INDEX_ID,
        [(*reference_period(lease_start), 100.0), (*reference_period(today), 130.0)],
        source="cbs",
    )
    make_renter(
        db_session,
        rent_escalation_mode="cpi",
        lease_start=lease_start,
        lease_end=lease_start + relativedelta(years=3),
        base_rent=5000.0,
        cpi_base_index=100.0,
        lease_years=[{"amount": 5000.0, "type": "contract"} for _ in range(3)],
    )
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)

    resp = _post(client, [FakeSource("cbs"), FakeSource("boi")])

    assert resp.json()["renters_updated"] == 1  # years 2 and 3 were repriced
    assert resp.json()["notified"] == 0
    assert _cpi_notifications(db_session) == []


def test_run_cpi_indexing_endpoint_503s_when_the_cache_is_stale(
    client, db_session, monkeypatch
):
    """The regression this whole change exists for: no source answering must not look
    like success. Before, this returned 200 every day for a week."""
    _seed_index(db_session)  # newest reading is 2023-11 — years behind
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)

    resp = _post(client, [FakeSource("cbs"), FakeSource("boi")])

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "stale"
    assert body["source"] is None
    assert body["latest_period"] == "2023-11"
    # The summary survives the non-2xx, so the scheduler's log line still explains why.
    assert body["stale_months"] > 2

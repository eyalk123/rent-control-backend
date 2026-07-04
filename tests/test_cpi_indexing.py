"""Full-stack tests for CPI rent linkage: create materializes amounts server-side,
and the internal cron endpoint refreshes them."""
import json
from datetime import date

from app.api.dependencies import get_cbs_index_service
from app.config import settings
from app.main import app
from app.repositories.cpi_index_repository import CpiIndexRepository
from tests.factories import make_renter

CRON_SECRET = "cpi-secret"
INDEX_ID = 120010


class FakeCbs:
    def __init__(self, all_rows=None, latest_rows=None):
        self._all = all_rows or []
        self._latest = latest_rows or []

    def fetch_all(self):
        return self._all

    def fetch_latest(self, n=6):
        return self._latest


def _seed_index(db_session):
    CpiIndexRepository(db_session).upsert_many(
        INDEX_ID, [(2022, 11, 100.0), (2023, 11, 110.0)]
    )


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


def test_run_cpi_indexing_endpoint_updates_renters(client, db_session, monkeypatch):
    _seed_index(db_session)
    renter = make_renter(
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
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)
    app.dependency_overrides[get_cbs_index_service] = lambda: FakeCbs()
    try:
        # Wrong / missing secret is rejected.
        assert client.post("/internal/run-cpi-indexing").status_code == 401

        resp = client.post(
            "/internal/run-cpi-indexing", headers={"X-Cron-Secret": CRON_SECRET}
        )
        assert resp.status_code == 200
        assert resp.json()["renters_updated"] == 1
    finally:
        app.dependency_overrides.pop(get_cbs_index_service, None)

    db_session.refresh(renter)
    assert [y["amount"] for y in json.loads(renter.lease_years)] == [5000, 5500]

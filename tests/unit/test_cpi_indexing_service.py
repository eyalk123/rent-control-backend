"""Unit tests for CPI rent linkage: the pure math, the known-index lookup, and the
monthly indexing job."""
import json
from datetime import date

from app.repositories.cpi_index_repository import CpiIndexRepository
from app.repositories.renter_repository import RenterRepository
from app.services.cpi_indexing_service import (
    CpiIndexingService,
    compute_cpi_amount,
    materialize_cpi_amounts,
)
from tests.factories import make_renter

INDEX_ID = 120010


class FakeCbs:
    """Stand-in for CbsIndexService — returns canned rows, no network."""

    def __init__(self, all_rows=None, latest_rows=None):
        self._all = all_rows or []
        self._latest = latest_rows or []

    def fetch_all(self):
        return self._all

    def fetch_latest(self, n=6):
        return self._latest


# --- pure math ---------------------------------------------------------------

def test_compute_cpi_amount_scales_by_ratio():
    # +2.6% index rise since signing.
    assert compute_cpi_amount(5000, base_index=103.0, known_index=105.6) == 5126


def test_compute_cpi_amount_floors_at_base_rent():
    # Deflation: index below base -> rent holds at base_rent, never lower.
    assert compute_cpi_amount(5000, base_index=100.0, known_index=90.0) == 5000


def test_compute_cpi_amount_falls_back_to_base_when_index_missing():
    assert compute_cpi_amount(5000, base_index=None, known_index=105.0) == 5000
    assert compute_cpi_amount(5000, base_index=100.0, known_index=None) == 5000


def test_materialize_amounts_multiyear_with_floor():
    lease_start = date(2020, 1, 1)
    # index by contract-year anniversary: up, down, back up.
    index_by_year = {0: 100.0, 1: 110.0, 2: 90.0, 3: 110.0}

    def lookup(d: date):
        return index_by_year[d.year - 2020]

    years = [{"amount": 5000, "type": "contract"} for _ in range(4)]
    out = materialize_cpi_amounts(years, lease_start, 5000, base_index=100.0, index_lookup=lookup)

    assert [y["amount"] for y in out] == [5000, 5500, 5000, 5500]
    # Types and count preserved.
    assert all(y["type"] == "contract" for y in out)


# --- known-index lookup (publication convention) -----------------------------

def test_latest_on_or_before_honors_publication_lag(db_session):
    repo = CpiIndexRepository(db_session)
    repo.upsert_many(INDEX_ID, [(2026, 4, 104.0), (2026, 5, 104.8), (2026, 6, 105.0)])

    # July 4: before the 15th, so June's reading (published July 15) is NOT yet
    # known — the latest known is May.
    assert repo.latest_on_or_before(INDEX_ID, date(2026, 7, 4)) == 104.8
    # July 20: past the 15th, so June (published July 15) is now known.
    assert repo.latest_on_or_before(INDEX_ID, date(2026, 7, 20)) == 105.0


def test_latest_on_or_before_none_when_unpublished(db_session):
    repo = CpiIndexRepository(db_session)
    assert repo.is_empty(INDEX_ID) is True
    repo.upsert_many(INDEX_ID, [(2026, 5, 104.8)])
    assert repo.is_empty(INDEX_ID) is False
    # Nothing published on/before this early date.
    assert repo.latest_on_or_before(INDEX_ID, date(2020, 1, 1)) is None


def test_upsert_many_updates_changed_values(db_session):
    repo = CpiIndexRepository(db_session)
    assert repo.upsert_many(INDEX_ID, [(2026, 5, 104.8)]) == 1
    assert repo.upsert_many(INDEX_ID, [(2026, 5, 104.8)]) == 0  # unchanged
    assert repo.upsert_many(INDEX_ID, [(2026, 5, 105.0)]) == 1  # revised


# --- the job -----------------------------------------------------------------

def _seed_cpi_renter(db_session, **kw):
    return make_renter(
        db_session,
        rent_escalation_mode="cpi",
        lease_start=date(2023, 1, 1),
        base_rent=5000.0,
        lease_years=[
            {"amount": 5000.0, "type": "contract"},
            {"amount": 5000.0, "type": "contract"},
        ],
        **kw,
    )


def test_run_cpi_indexing_recomputes_amounts(db_session):
    CpiIndexRepository(db_session).upsert_many(
        INDEX_ID,
        # base (known at 2023-01-01 -> ref 2022-11) and year-2 (known at 2024-01-01 -> ref 2023-11)
        [(2022, 11, 100.0), (2023, 11, 110.0)],
    )
    renter = _seed_cpi_renter(db_session, cpi_base_index=100.0)

    service = CpiIndexingService(
        RenterRepository(db_session), CpiIndexRepository(db_session), FakeCbs()
    )
    summary = service.run_cpi_indexing()

    assert summary["renters_updated"] == 1
    db_session.refresh(renter)
    amounts = [y["amount"] for y in json.loads(renter.lease_years)]
    assert amounts == [5000, 5500]  # year 1 = 5000 x 110/100


def test_run_cpi_indexing_freezes_base_index_when_missing(db_session):
    CpiIndexRepository(db_session).upsert_many(INDEX_ID, [(2022, 11, 100.0), (2023, 11, 110.0)])
    renter = _seed_cpi_renter(db_session, cpi_base_index=None)

    CpiIndexingService(
        RenterRepository(db_session), CpiIndexRepository(db_session), FakeCbs()
    ).run_cpi_indexing()

    db_session.refresh(renter)
    assert renter.cpi_base_index == 100.0
    assert [y["amount"] for y in json.loads(renter.lease_years)] == [5000, 5500]


# --- CBS API client (parsing + pagination, no network) -----------------------

def test_cbs_parse_extracts_and_skips_incomplete():
    from app.services.cbs_index_service import CbsIndexService

    data = {
        "month": [
            {
                "date": [
                    {"year": 2026, "month": 5, "currBase": {"value": 104.8}},
                    {"year": 2026, "month": 4, "currBase": {"value": 105.1}},
                    {"year": 2026, "month": 3, "currBase": None},  # skipped
                ]
            }
        ]
    }
    assert CbsIndexService._parse(data) == [(2026, 5, 104.8), (2026, 4, 105.1)]


def test_cbs_fetch_all_follows_pagination(monkeypatch):
    from app.services import cbs_index_service as mod

    pages = {
        None: {
            "month": [{"date": [{"year": 2026, "month": 5, "currBase": {"value": 104.8}}]}],
            "paging": {"next_url": "http://x/page2"},
        },
        "http://x/page2": {
            "month": [{"date": [{"year": 2026, "month": 4, "currBase": {"value": 105.1}}]}],
            "paging": {"next_url": None},
        },
    }

    class FakeResp:
        def __init__(self, d):
            self._d = d

        def raise_for_status(self):
            pass

        def json(self):
            return self._d

    def fake_get(url, params=None, timeout=None):
        # First page is requested with params; subsequent pages via the bare next_url.
        key = None if params is not None else url
        return FakeResp(pages[key])

    monkeypatch.setattr(mod.requests, "get", fake_get)
    rows = mod.CbsIndexService("http://x", 120010).fetch_all()
    assert rows == [(2026, 5, 104.8), (2026, 4, 105.1)]


def test_run_cpi_indexing_backfills_when_empty(db_session):
    # Empty cache -> job calls fetch_all and seeds it.
    fake = FakeCbs(all_rows=[(2022, 11, 100.0), (2023, 11, 110.0)])
    _seed_cpi_renter(db_session, cpi_base_index=100.0)

    summary = CpiIndexingService(
        RenterRepository(db_session), CpiIndexRepository(db_session), fake
    ).run_cpi_indexing()

    assert summary["fetched"] == 2
    assert CpiIndexRepository(db_session).is_empty(INDEX_ID) is False

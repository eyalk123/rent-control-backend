"""Unit tests for CPI rent linkage: the pure math, the known-index lookup, and the
monthly indexing job."""
import json
from datetime import date

from app.repositories.cpi_index_repository import CpiIndexRepository
from app.repositories.renter_repository import RenterRepository
from app.services.cpi_indexing_service import (
    CpiIndexingService,
    compute_cpi_amount,
    has_cpi_rule,
    materialize_cpi_amounts,
    materialize_ruled_lease_years,
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


# --- per-year rules ("custom" mode): the forward walk ------------------------

def _year(amount, rule=None, type_="contract"):
    y = {"amount": amount, "type": type_}
    if rule is not None:
        y["rule"] = rule
    return y


def _no_index(_d: date):
    return None


def test_ruled_years_walk_percent_and_fixed_from_previous_amount():
    years = [
        _year(5000),
        _year(0, {"mode": "percent", "value": 3}),
        _year(0, {"mode": "fixed", "value": 200}),
        _year(0, {"mode": "none"}),
    ]
    out = materialize_ruled_lease_years(years, date(2026, 1, 1), 5000, _no_index)
    # 5000 -> +3% -> 5150 -> +200 -> 5350 -> same -> 5350
    assert [y["amount"] for y in out] == [5000, 5150, 5350, 5350]


def test_ruled_years_manual_and_legacy_ruleless_amounts_are_kept():
    years = [
        _year(5000),
        _year(7777, {"mode": "manual"}),
        _year(8888),  # no rule at all — a year stored before rules existed
        _year(0, {"mode": "percent", "value": 10}),
    ]
    out = materialize_ruled_lease_years(years, date(2026, 1, 1), 5000, _no_index)
    # Hand-typed amounts survive, and the percent year steps off the one before it.
    assert [y["amount"] for y in out] == [5000, 7777, 8888, 9777]


def test_ruled_years_chained_cpi_indexes_off_the_previous_year():
    lease_start = date(2026, 1, 1)
    index_by_year = {2026: 100.0, 2027: 110.0, 2028: 121.0}
    years = [_year(5000), _year(0, {"mode": "cpi"}), _year(0, {"mode": "cpi"})]

    out = materialize_ruled_lease_years(
        years, lease_start, 5000, lambda d: index_by_year.get(d.year)
    )
    # Each CPI year moves by that year's index change, not the whole-lease ratio.
    assert [y["amount"] for y in out] == [5000, 5500, 6050]


def test_ruled_years_chained_cpi_floors_at_previous_year():
    lease_start = date(2026, 1, 1)
    index_by_year = {2026: 100.0, 2027: 90.0}  # deflation
    years = [_year(5000), _year(0, {"mode": "cpi"})]

    out = materialize_ruled_lease_years(
        years, lease_start, 5000, lambda d: index_by_year.get(d.year)
    )
    assert [y["amount"] for y in out] == [5000, 5000]  # never decreases


def test_ruled_years_percent_after_cpi_steps_off_the_resolved_cpi_amount():
    """The case the feature exists for: a rule *after* a CPI year. The client can't
    price this, but the walk knows the CPI year's real amount by the time it gets here."""
    lease_start = date(2026, 1, 1)
    index_by_year = {2026: 100.0, 2027: 110.0}
    years = [
        _year(5000),
        _year(0, {"mode": "cpi"}),
        _year(0, {"mode": "percent", "value": 5}),
    ]

    out = materialize_ruled_lease_years(
        years, lease_start, 5000, lambda d: index_by_year.get(d.year)
    )
    # 5000 -> CPI +10% -> 5500 -> +5% -> 5775 (not 5250, which is 5% on the base)
    assert [y["amount"] for y in out] == [5000, 5500, 5775]


def test_ruled_years_unpublished_cpi_projects_flat_then_self_heals():
    lease_start = date(2026, 1, 1)
    years = [
        _year(5000),
        _year(0, {"mode": "cpi"}),
        _year(0, {"mode": "percent", "value": 5}),
    ]

    # Year 2's anniversary index isn't published yet -> CPI year projects flat, and the
    # percent year downstream steps off that projection.
    unpublished = {2026: 100.0}
    projected = materialize_ruled_lease_years(
        years, lease_start, 5000, lambda d: unpublished.get(d.year)
    )
    assert [y["amount"] for y in projected] == [5000, 5000, 5250]

    # Once the reading lands, a later job run recomputes the whole walk — including the
    # downstream percent year.
    published = {2026: 100.0, 2027: 110.0}
    healed = materialize_ruled_lease_years(
        years, lease_start, 5000, lambda d: published.get(d.year)
    )
    assert [y["amount"] for y in healed] == [5000, 5500, 5775]


def test_ruled_years_preserve_type_and_rule():
    years = [
        _year(5000, type_="contract"),
        _year(0, {"mode": "percent", "value": 3}, type_="option"),
    ]
    out = materialize_ruled_lease_years(years, date(2026, 1, 1), 5000, _no_index)
    assert [y["type"] for y in out] == ["contract", "option"]
    assert out[1]["rule"] == {"mode": "percent", "value": 3}


def test_has_cpi_rule():
    assert has_cpi_rule([_year(5000), _year(0, {"mode": "cpi"})]) is True
    assert has_cpi_rule([_year(5000), _year(0, {"mode": "percent", "value": 3})]) is False
    assert has_cpi_rule([_year(5000), _year(5000)]) is False  # legacy, rule-less


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


def test_run_cpi_indexing_picks_up_custom_lease_with_a_cpi_year(db_session):
    CpiIndexRepository(db_session).upsert_many(
        INDEX_ID, [(2022, 11, 100.0), (2023, 11, 110.0)]
    )
    renter = make_renter(
        db_session,
        rent_escalation_mode="custom",
        lease_start=date(2023, 1, 1),
        base_rent=5000.0,
        lease_years=[
            {"amount": 5000.0, "type": "contract"},
            {"amount": 5000.0, "type": "contract", "rule": {"mode": "cpi"}},
        ],
    )

    summary = CpiIndexingService(
        RenterRepository(db_session), CpiIndexRepository(db_session), FakeCbs()
    ).run_cpi_indexing()

    assert summary["renters_updated"] == 1
    db_session.refresh(renter)
    assert [y["amount"] for y in json.loads(renter.lease_years)] == [5000, 5500]
    # Chained linkage needs no frozen base index.
    assert renter.cpi_base_index is None


def test_run_cpi_indexing_skips_custom_lease_with_no_cpi_year(db_session):
    CpiIndexRepository(db_session).upsert_many(INDEX_ID, [(2022, 11, 100.0)])
    renter = make_renter(
        db_session,
        rent_escalation_mode="custom",
        lease_start=date(2023, 1, 1),
        base_rent=5000.0,
        lease_years=[
            {"amount": 5000.0, "type": "contract"},
            {"amount": 6000.0, "type": "contract", "rule": {"mode": "manual"}},
        ],
    )

    summary = CpiIndexingService(
        RenterRepository(db_session), CpiIndexRepository(db_session), FakeCbs()
    ).run_cpi_indexing()

    assert summary["renters_updated"] == 0
    db_session.refresh(renter)
    assert [y["amount"] for y in json.loads(renter.lease_years)] == [5000.0, 6000.0]


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

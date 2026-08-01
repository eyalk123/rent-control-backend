"""Unit tests for CPI rent linkage: the pure math, the known-index lookup, and the
indexing job."""
import json
from datetime import date

from app.repositories.cpi_index_repository import CpiIndexRepository, reference_period
from app.repositories.renter_repository import RenterRepository
from app.services import index_source
from app.services.boi_index_service import BoiIndexService
from app.services.cpi_indexing_service import (
    CpiIndexingService,
    compute_cpi_amount,
    has_cpi_rule,
    materialize_cpi_amounts,
    materialize_ruled_lease_years,
)
from tests.factories import make_renter

INDEX_ID = 120010


class FakeSource:
    """Stand-in for an IndexSource — returns canned rows, no network. ``calls`` counts
    fetches so tests can assert a source was never reached."""

    name = "fake"

    def __init__(self, all_rows=None, latest_rows=None):
        self._all = all_rows or []
        self._latest = latest_rows or []
        self.calls = 0

    def fetch_all(self):
        self.calls += 1
        return self._all

    def fetch_latest(self, n=6):
        self.calls += 1
        return self._latest


class FakeCbs(FakeSource):
    name = "cbs"


class FakeBoi(FakeSource):
    name = "boi"


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
    repo.upsert_many(
        INDEX_ID, [(2026, 4, 104.0), (2026, 5, 104.8), (2026, 6, 105.0)], source="cbs"
    )

    # July 4: before the 15th, so June's reading (published July 15) is NOT yet
    # known — the latest known is May.
    assert repo.latest_on_or_before(INDEX_ID, date(2026, 7, 4)) == 104.8
    # July 20: past the 15th, so June (published July 15) is now known.
    assert repo.latest_on_or_before(INDEX_ID, date(2026, 7, 20)) == 105.0


def test_latest_on_or_before_none_when_unpublished(db_session):
    repo = CpiIndexRepository(db_session)
    assert repo.is_empty(INDEX_ID) is True
    repo.upsert_many(INDEX_ID, [(2026, 5, 104.8)], source="cbs")
    assert repo.is_empty(INDEX_ID) is False
    # Nothing published on/before this early date.
    assert repo.latest_on_or_before(INDEX_ID, date(2020, 1, 1)) is None


def test_upsert_many_updates_changed_values(db_session):
    repo = CpiIndexRepository(db_session)
    assert repo.upsert_many(INDEX_ID, [(2026, 5, 104.8)], source="cbs")[0] == 1
    assert repo.upsert_many(INDEX_ID, [(2026, 5, 104.8)], source="cbs")[0] == 0  # unchanged
    assert repo.upsert_many(INDEX_ID, [(2026, 5, 105.0)], source="cbs")[0] == 1  # revised


# --- source precedence: a fallback fills gaps but never overrules CBS ---------

def test_upsert_many_fallback_does_not_overwrite_cbs(db_session):
    repo = CpiIndexRepository(db_session)
    repo.upsert_many(INDEX_ID, [(2026, 5, 104.8)], source="cbs")

    # BOI disagrees about a month CBS already published — CBS stands.
    changed, superseded = repo.upsert_many(INDEX_ID, [(2026, 5, 99.9)], source="boi")

    assert (changed, superseded) == (0, set())
    assert repo.latest_on_or_before(INDEX_ID, date(2026, 7, 20)) == 104.8


def test_upsert_many_fallback_fills_gaps(db_session):
    repo = CpiIndexRepository(db_session)
    repo.upsert_many(INDEX_ID, [(2026, 5, 104.8)], source="cbs")

    # A month CBS hasn't covered is fair game for the fallback.
    changed, superseded = repo.upsert_many(INDEX_ID, [(2026, 6, 105.0)], source="boi")

    assert changed == 1
    assert superseded == set()
    assert repo.latest_period(INDEX_ID) == (2026, 6)


def test_upsert_many_cbs_supersedes_fallback(db_session):
    repo = CpiIndexRepository(db_session)
    repo.upsert_many(INDEX_ID, [(2026, 5, 99.9)], source="boi")

    # CBS comes back and reclaims the month, reporting it so frozen values re-derive.
    changed, superseded = repo.upsert_many(INDEX_ID, [(2026, 5, 104.8)], source="cbs")

    assert changed == 1
    assert superseded == {(2026, 5)}
    assert repo.latest_on_or_before(INDEX_ID, date(2026, 7, 20)) == 104.8
    # ...and a second CBS run is now a no-op: the month has already changed hands.
    assert repo.upsert_many(INDEX_ID, [(2026, 5, 104.8)], source="cbs") == (0, set())


def test_latest_period_reports_newest_month(db_session):
    repo = CpiIndexRepository(db_session)
    assert repo.latest_period(INDEX_ID) is None
    repo.upsert_many(INDEX_ID, [(2025, 12, 100.0), (2026, 3, 101.0)], source="cbs")
    assert repo.latest_period(INDEX_ID) == (2026, 3)


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
        source="cbs",
    )
    renter = _seed_cpi_renter(db_session, cpi_base_index=100.0)

    service = CpiIndexingService(
        RenterRepository(db_session), CpiIndexRepository(db_session), [FakeCbs()]
    )
    summary = service.run_cpi_indexing()

    assert summary["renters_updated"] == 1
    db_session.refresh(renter)
    amounts = [y["amount"] for y in json.loads(renter.lease_years)]
    assert amounts == [5000, 5500]  # year 1 = 5000 x 110/100


def test_run_cpi_indexing_freezes_base_index_when_missing(db_session):
    CpiIndexRepository(db_session).upsert_many(
        INDEX_ID, [(2022, 11, 100.0), (2023, 11, 110.0)], source="cbs"
    )
    renter = _seed_cpi_renter(db_session, cpi_base_index=None)

    CpiIndexingService(
        RenterRepository(db_session), CpiIndexRepository(db_session), [FakeCbs()]
    ).run_cpi_indexing()

    db_session.refresh(renter)
    assert renter.cpi_base_index == 100.0
    assert [y["amount"] for y in json.loads(renter.lease_years)] == [5000, 5500]


def test_run_cpi_indexing_picks_up_custom_lease_with_a_cpi_year(db_session):
    CpiIndexRepository(db_session).upsert_many(
        INDEX_ID, [(2022, 11, 100.0), (2023, 11, 110.0)], source="cbs"
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
        RenterRepository(db_session), CpiIndexRepository(db_session), [FakeCbs()]
    ).run_cpi_indexing()

    assert summary["renters_updated"] == 1
    db_session.refresh(renter)
    assert [y["amount"] for y in json.loads(renter.lease_years)] == [5000, 5500]
    # Chained linkage needs no frozen base index.
    assert renter.cpi_base_index is None


def test_run_cpi_indexing_skips_custom_lease_with_no_cpi_year(db_session):
    CpiIndexRepository(db_session).upsert_many(INDEX_ID, [(2022, 11, 100.0)], source="cbs")
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
        RenterRepository(db_session), CpiIndexRepository(db_session), [FakeCbs()]
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

    # The HTTP call is shared by every source, so it lives in index_source.
    monkeypatch.setattr(index_source.requests, "get", fake_get)
    rows = mod.CbsIndexService("http://x", 120010).fetch_all()
    assert rows == [(2026, 5, 104.8), (2026, 4, 105.1)]


def test_fetch_json_returns_none_on_failure(monkeypatch):
    """The contract every source relies on: upstream trouble is an empty result, never
    an exception — otherwise one bad government API takes the whole job down."""
    import logging

    import requests

    def boom(url, params=None, timeout=None):
        raise requests.ConnectTimeout("connection timed out")

    monkeypatch.setattr(index_source.requests, "get", boom)
    assert index_source.fetch_json("http://x", {}, logging.getLogger(__name__), "CBS") is None


# --- BOI API client (SDMX parsing, no network) -------------------------------

def _sdmx(periods, observations):
    return {
        "data": {
            "dataSets": [{"series": {"0:0:0:0:0:0:0:0:0:0:0:0:0:0": {
                "observations": observations
            }}}],
            "structure": {"dimensions": {"observation": [
                {"id": "TIME_PERIOD", "values": [{"id": p} for p in periods]}
            ]}},
        }
    }


def test_boi_parse_maps_positions_to_periods():
    # Observation keys are positions into TIME_PERIOD, and values arrive as strings.
    data = _sdmx(["2026-05", "2026-06"], {"0": ["104.8"], "1": ["104.8"]})
    assert BoiIndexService._parse(data) == [(2026, 5, 104.8), (2026, 6, 104.8)]


def test_boi_parse_skips_unusable_observations():
    data = _sdmx(
        ["2026-05", "2026", "2026-13", "2026-06"],
        {
            "0": ["104.8"],
            "1": ["100.0"],  # annual period — not a monthly reading
            "2": ["100.0"],  # impossible month
            "3": [None],     # published as a gap
            "9": ["1.0"],    # position past the end of TIME_PERIOD
        },
    )
    assert BoiIndexService._parse(data) == [(2026, 5, 104.8)]


def test_boi_parse_tolerates_an_empty_response():
    assert BoiIndexService._parse({}) == []
    assert BoiIndexService._parse({"data": {"structure": {"dimensions": {}}}}) == []


def test_run_cpi_indexing_backfills_when_empty(db_session):
    # Empty cache -> job calls fetch_all and seeds it.
    fake = FakeCbs(all_rows=[(2022, 11, 100.0), (2023, 11, 110.0)])
    _seed_cpi_renter(db_session, cpi_base_index=100.0)

    summary = CpiIndexingService(
        RenterRepository(db_session), CpiIndexRepository(db_session), [fake]
    ).run_cpi_indexing()

    assert summary["fetched"] == 2
    assert CpiIndexRepository(db_session).is_empty(INDEX_ID) is False


# --- source chain: CBS first, fallback only when it can't answer -------------

def _run(db_session, sources):
    return CpiIndexingService(
        RenterRepository(db_session), CpiIndexRepository(db_session), sources
    ).run_cpi_indexing()


def test_primary_source_wins_and_fallback_is_never_called(db_session):
    cbs = FakeCbs(all_rows=[(2022, 11, 100.0), (2023, 11, 110.0)])
    boi = FakeBoi(all_rows=[(2022, 11, 999.0)])

    summary = _run(db_session, [cbs, boi])

    assert summary["source"] == "cbs"
    assert summary["degraded"] is False
    assert boi.calls == 0  # not even asked
    assert CpiIndexRepository(db_session).latest_on_or_before(INDEX_ID, date(2024, 1, 1)) == 110.0


def test_falls_back_when_primary_returns_nothing(db_session):
    # What the CBS outage looks like: the fetch "succeeds" with no rows.
    cbs = FakeCbs(all_rows=[])
    boi = FakeBoi(all_rows=[(2022, 11, 100.0), (2023, 11, 110.0)])
    renter = _seed_cpi_renter(db_session, cpi_base_index=None)

    summary = _run(db_session, [cbs, boi])

    assert summary["source"] == "boi"
    assert summary["degraded"] is True  # working, but not off the authoritative feed
    assert summary["fetched"] == 2
    # The point of the fallback: rents actually move again.
    db_session.refresh(renter)
    assert renter.cpi_base_index == 100.0
    assert [y["amount"] for y in json.loads(renter.lease_years)] == [5000, 5500]


def test_reports_when_no_source_answers(db_session):
    renter = _seed_cpi_renter(db_session, cpi_base_index=None)

    summary = _run(db_session, [FakeCbs(), FakeBoi()])

    assert summary["source"] is None
    assert summary["fetched"] == 0
    assert summary["degraded"] is False  # nothing served, so nothing is "degraded"
    assert summary["stale"] is True  # ...but the empty cache is loudly wrong
    db_session.refresh(renter)
    assert renter.cpi_base_index is None


def test_cbs_return_supersedes_fallback_and_refreezes_base(db_session):
    """The reconciliation that stops a base frozen off the fallback from being permanent."""
    boi_rows = [(2022, 11, 99.0), (2023, 11, 110.0)]
    _run(db_session, [FakeCbs(), FakeBoi(all_rows=boi_rows)])
    renter = _seed_cpi_renter(db_session, cpi_base_index=None)
    _run(db_session, [FakeCbs(), FakeBoi(latest_rows=boi_rows)])
    db_session.refresh(renter)
    assert renter.cpi_base_index == 99.0  # frozen against a provisional reading

    # CBS comes back with the real figure for the base month.
    cbs_rows = [(2022, 11, 100.0), (2023, 11, 110.0)]
    summary = _run(db_session, [FakeCbs(latest_rows=cbs_rows), FakeBoi()])

    assert summary["degraded"] is False
    db_session.refresh(renter)
    assert renter.cpi_base_index == 100.0  # re-anchored, not stuck on 99.0
    assert [y["amount"] for y in json.loads(renter.lease_years)] == [5000, 5500]


def test_base_index_is_not_refrozen_on_an_ordinary_run(db_session):
    """A frozen base is contractual — only a superseded reading may move it."""
    CpiIndexRepository(db_session).upsert_many(
        INDEX_ID, [(2022, 11, 100.0), (2023, 11, 110.0)], source="cbs"
    )
    renter = _seed_cpi_renter(db_session, cpi_base_index=103.0)  # frozen at signing

    _run(db_session, [FakeCbs(latest_rows=[(2023, 11, 110.0)])])

    db_session.refresh(renter)
    assert renter.cpi_base_index == 103.0


# --- staleness: the check that would have caught the outage ------------------

def _fresh_row(value=110.0):
    """A reading for the newest published month, so a test stays off the staleness path."""
    year, month = reference_period(date.today())
    return (year, month, value)


def _seed_at_offset(db_session, months_back, source="cbs"):
    """Seed one reading ``months_back`` months behind the newest published month."""
    year, month = reference_period(date.today())
    key = year * 12 + (month - 1) - months_back
    CpiIndexRepository(db_session).upsert_many(
        INDEX_ID, [(key // 12, key % 12 + 1, 100.0)], source=source
    )


def test_fresh_cache_is_not_stale(db_session):
    _seed_at_offset(db_session, 0)
    summary = _run(db_session, [FakeCbs()])
    assert summary["stale_months"] == 0
    assert summary["stale"] is False


def test_cache_within_the_allowed_window_is_not_stale(db_session):
    _seed_at_offset(db_session, 2)  # CPI_MAX_STALE_MONTHS
    summary = _run(db_session, [FakeCbs()])
    assert summary["stale_months"] == 2
    assert summary["stale"] is False


def test_cache_beyond_the_window_is_stale(db_session):
    _seed_at_offset(db_session, 4)
    summary = _run(db_session, [FakeCbs()])
    assert summary["stale_months"] == 4
    assert summary["stale"] is True


def test_empty_cache_is_stale(db_session):
    summary = _run(db_session, [FakeCbs()])
    assert summary["latest_period"] is None
    assert summary["stale_months"] is None
    assert summary["stale"] is True


# --- history floor -----------------------------------------------------------

def test_history_floor_reaches_two_months_below_the_earliest_lease_year():
    """A lease starting 2020-01-01 reads the index known then — 2019-11 — so the floor has
    to sit below the intended earliest lease year, not on it."""
    year, month = reference_period(date(2020, 1, 1))
    assert (year, month) == (2019, 11)
    assert index_source.HISTORY_FLOOR <= (year, month)


def test_at_or_after_floor_drops_older_readings():
    rows = [(1951, 9, 0.00026), (2019, 10, 88.0), (2019, 11, 88.4), (2026, 6, 104.8)]
    assert index_source.at_or_after_floor(rows) == [(2019, 11, 88.4), (2026, 6, 104.8)]


def test_cbs_fetch_all_stops_paging_past_the_floor(monkeypatch):
    """CBS has no start-date filter and returns newest-first, so paging stops at the floor
    instead of pulling the whole series back to 1951."""
    from app.services import cbs_index_service as mod

    pages = {
        None: {
            "month": [{"date": [{"year": 2026, "month": 5, "currBase": {"value": 104.8}}]}],
            "paging": {"next_url": "http://x/page2"},
        },
        "http://x/page2": {  # entirely below the floor -> stop, don't request page 3
            "month": [{"date": [{"year": 2019, "month": 1, "currBase": {"value": 80.0}}]}],
            "paging": {"next_url": "http://x/page3"},
        },
    }
    requested = []

    class FakeResp:
        def __init__(self, d):
            self._d = d

        def raise_for_status(self):
            pass

        def json(self):
            return self._d

    def fake_get(url, params=None, timeout=None):
        key = None if params is not None else url
        requested.append(key)
        return FakeResp(pages[key])

    monkeypatch.setattr(index_source.requests, "get", fake_get)
    rows = mod.CbsIndexService("http://x", 120010).fetch_all()

    assert rows == [(2026, 5, 104.8)]
    assert requested == [None, "http://x/page2"]  # page3 never fetched


def test_lease_older_than_the_cached_history_is_counted(db_session):
    """The floor's cost, made visible: such a lease holds at base rent, and the job says so
    rather than letting it quietly undercharge."""
    floor_year, floor_month = index_source.HISTORY_FLOOR
    CpiIndexRepository(db_session).upsert_many(
        INDEX_ID, [(floor_year, floor_month, 88.4), _fresh_row()], source="cbs"
    )
    renter = make_renter(
        db_session,
        rent_escalation_mode="cpi",
        lease_start=date(2015, 1, 1),  # predates the cached history
        base_rent=5000.0,
        lease_years=[{"amount": 5000.0, "type": "contract"}] * 2,
    )

    summary = _run(db_session, [FakeCbs()])

    assert summary["renters_before_index_start"] == 1
    db_session.refresh(renter)
    assert renter.cpi_base_index is None
    assert [y["amount"] for y in json.loads(renter.lease_years)] == [5000, 5000]


def test_lease_at_the_floor_resolves_normally(db_session):
    floor_year, floor_month = index_source.HISTORY_FLOOR
    CpiIndexRepository(db_session).upsert_many(
        INDEX_ID, [(floor_year, floor_month, 100.0), _fresh_row(110.0)], source="cbs"
    )
    renter = make_renter(
        db_session,
        rent_escalation_mode="cpi",
        lease_start=date(2020, 1, 1),  # the earliest lease year the floor is chosen for
        base_rent=5000.0,
        lease_years=[{"amount": 5000.0, "type": "contract"}] * 2,
    )

    summary = _run(db_session, [FakeCbs()])

    assert summary["renters_before_index_start"] == 0
    db_session.refresh(renter)
    assert renter.cpi_base_index == 100.0

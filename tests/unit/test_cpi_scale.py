"""CPI readings published on different index bases are converted to one scale before they
are cached, so a lease whose base index and anniversary index straddle a CBS base change is
priced the way the CBS linkage calculator prices it.

The figures are real: CBS published November 2023 as 105.1 and November 2024 as 108.7 on
the "2022 average" base, November 2025 as 103.6 on the "2024 average" one, and its
calculator gives 1.074 as the coefficient between the two bases."""
import json
from datetime import date

import pytest

from app.models.cpi_base_factor import CpiBaseFactor
from app.repositories.cpi_base_factor_repository import CpiBaseFactorRepository
from app.repositories.cpi_index_repository import CpiIndexRepository
from app.repositories.renter_repository import RenterRepository
from app.services import country_service
from app.services.boi_index_service import BoiIndexService
from app.services.cpi_indexing_service import CpiIndexingService
from app.services.cpi_scale import CpiScale, base_key
from tests.factories import make_renter

INDEX_ID = 120010
OLD, NEW = "2022 ממוצע", "2024 ממוצע"


def _series():
    return country_service.index_series_by_country()["IL"]


class FakeCalculator:
    """Stands in for the CBS calculator: answers with a coefficient per pair of bases, and
    echoes the months it was asked about the way the real one does ("2024-12" → "2024-12",
    non-padded months included)."""

    def __init__(self, coefficients: dict[tuple[str, str], float], base_of: dict):
        self.coefficients = coefficients
        self.base_of = base_of  # (year, month) -> base label as CBS writes it
        self.asked: list[tuple] = []

    # Set on the class as an instance, so it is not bound: no CpiScale `self` arrives.
    def __call__(self, index_id, first, second):
        self.asked.append((first, second))
        pair = (base_key(self.base_of[first]), base_key(self.base_of[second]))
        return {
            "from_index_date": f"{first[0]}-{first[1]}",
            "to_index_date": f"{second[0]}-{second[1]}",
            "base_year": self.base_of[first],
            "chaining_coefficient": self.coefficients[pair],
        }


@pytest.fixture
def calculator(monkeypatch):
    calc = FakeCalculator(
        {("2022", "2024"): 1.074, ("2024", "2026"): 1.05},
        {
            (2024, 11): OLD,
            (2024, 12): OLD,
            (2025, 1): NEW,
            (2027, 3): "2026 ממוצע",
        },
    )
    monkeypatch.setattr(CpiScale, "_ask_calculator", calc)
    return calc


def _scale(db_session):
    return CpiScale(CpiBaseFactorRepository(db_session), "http://cbs")


class FakeCbs:
    name = "cbs"

    def __init__(self, rows):
        self.rows = rows

    def fetch_all(self):
        return self.rows

    def fetch_latest(self, n=6):
        return self.rows


# --- base labels ---------------------------------------------------------------

def test_base_key_spells_both_publishers_the_same():
    assert base_key("2024 ממוצע") == "2024"
    assert base_key("I24_L") == "2024"
    assert base_key(None) is None
    # A base that is not a year average is kept as written, and still matches itself.
    assert base_key("ספטמבר 1951") == "ספטמבר 1951"


def test_boi_parse_names_the_base_from_the_unit_dimension():
    data = {
        "data": {
            "dataSets": [{"series": {"0:0:0:0:0:0:0:0:0:0:0:0:0:0": {
                "observations": {"0": ["101.5"]}
            }}}],
            "structure": {"dimensions": {
                "observation": [{"id": "TIME_PERIOD", "values": [{"id": "2025-01"}]}],
                "series": [{"id": f"D{i}", "values": [{"id": "x"}]} for i in range(8)]
                + [{"id": "UNIT_MEASURE", "values": [{"id": "I24_L"}]}],
            }},
        }
    }
    assert BoiIndexService._parse(data) == [(2025, 1, 101.5, "I24_L")]


# --- conversion ----------------------------------------------------------------

def test_readings_on_the_stored_scale_pass_through(db_session, calculator):
    rows, dropped = _scale(db_session).normalize(_series(), [(2025, 11, 103.6, NEW), (2025, 12, 103.7, None)])
    assert rows == [(2025, 11, 103.6), (2025, 12, 103.7)]
    assert dropped == 0
    assert calculator.asked == []


def test_an_older_base_is_divided_by_the_cbs_coefficient(db_session, calculator):
    rows, dropped = _scale(db_session).normalize(
        _series(), [(2024, 11, 108.7, OLD), (2024, 12, 108.4, OLD)]
    )
    assert dropped == 0
    assert rows == [(2024, 11, pytest.approx(108.7 / 1.074)), (2024, 12, pytest.approx(108.4 / 1.074))]
    # Asked once, about the group's newest month against the scale's own month.
    assert calculator.asked == [((2024, 12), (2025, 1))]
    stored = db_session.query(CpiBaseFactor).one()
    assert (stored.base, stored.coefficient, stored.from_month, stored.to_month) == (
        "2022", 1.074, "2024-12", "2025-01"
    )


def test_a_stored_coefficient_is_never_fetched_again(db_session, calculator):
    scale = _scale(db_session)
    scale.normalize(_series(), [(2024, 12, 108.4, OLD)])
    scale.normalize(_series(), [(2024, 11, 108.7, OLD)])
    assert len(calculator.asked) == 1


def test_a_newer_base_is_multiplied_by_the_coefficient(db_session, calculator):
    """The base change expected in January 2027: CBS restarts near 100 on the 2026 average,
    and the cache keeps reading on the 2024 scale."""
    rows, _ = _scale(db_session).normalize(_series(), [(2027, 3, 100.4, "2026 ממוצע")])
    assert rows == [(2027, 3, pytest.approx(100.4 * 1.05))]
    assert calculator.asked == [((2025, 1), (2027, 3))]


def test_readings_are_left_out_when_the_calculator_answers_something_else(db_session, monkeypatch):
    def wrong_months(scale, index_id, first, second):
        return {
            "from_index_date": "2024-10",
            "to_index_date": "2025-1",
            "base_year": OLD,
            "chaining_coefficient": 1.074,
        }

    monkeypatch.setattr(CpiScale, "_ask_calculator", wrong_months)
    rows, dropped = _scale(db_session).normalize(_series(), [(2024, 12, 108.4, OLD), (2025, 1, 101.5, NEW)])
    assert rows == [(2025, 1, 101.5)]
    assert dropped == 1
    assert db_session.query(CpiBaseFactor).count() == 0


def test_readings_are_left_out_when_cbs_is_unreachable(db_session, monkeypatch):
    monkeypatch.setattr(CpiScale, "_ask_calculator", lambda *a: None)
    rows, dropped = _scale(db_session).normalize(_series(), [(2024, 12, 108.4, OLD)])
    assert (rows, dropped) == ([], 1)


# --- end to end: a lease across the January 2025 base change ---------------------

def test_a_lease_across_a_base_change_is_priced_like_the_cbs_calculator(db_session, calculator):
    calculator.base_of.update({(2023, 11): OLD, (2025, 11): NEW})
    renter = make_renter(
        db_session,
        rent_escalation_mode="cpi",
        lease_start=date(2024, 1, 1),
        base_rent=5000.0,
        lease_years=[{"amount": 5000.0, "type": "contract"} for _ in range(3)],
    )
    source = FakeCbs([(2023, 11, 105.1, OLD), (2024, 11, 108.7, OLD), (2025, 11, 103.6, NEW)])

    summary = CpiIndexingService(
        RenterRepository(db_session),
        CpiIndexRepository(db_session),
        [source],
        scale=_scale(db_session),
    ).run_cpi_indexing(today=date(2026, 2, 1))

    assert summary["degraded"] is False
    db_session.refresh(renter)
    amounts = [y["amount"] for y in json.loads(renter.lease_years)]
    # Year two is on one base either way (108.7 / 105.1). Year three crosses the change:
    # 103.6 × 1.074 / 105.1 = +5.87%, where dividing the raw numbers would have held it
    # at 5,000.
    assert amounts == [5000, 5171, 5293]


def test_a_reading_that_cannot_be_converted_marks_the_run_degraded(db_session, monkeypatch):
    monkeypatch.setattr(CpiScale, "_ask_calculator", lambda *a: None)
    source = FakeCbs([(2024, 12, 108.4, OLD), (2025, 1, 101.5, NEW)])

    summary = CpiIndexingService(
        RenterRepository(db_session),
        CpiIndexRepository(db_session),
        [source],
        scale=_scale(db_session),
    ).run_cpi_indexing(today=date(2025, 3, 1))

    assert summary["degraded"] is True
    # Only the reading already on the scale was stored.
    assert CpiIndexRepository(db_session).latest_on_or_before(INDEX_ID, date(2025, 3, 1)) == 101.5
    assert CpiIndexRepository(db_session).earliest_period(INDEX_ID) == (2025, 1)

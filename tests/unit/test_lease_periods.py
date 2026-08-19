"""Where each lease period starts and ends, once a period can be shorter than a year."""
from datetime import date

import pytest

from app.services.lease_periods import (
    contract_end,
    period_index_for_month,
    period_index_on,
    period_months,
    period_start,
    schedule_end,
    total_months,
)

START = date(2026, 1, 1)


def years(*specs) -> list[dict]:
    """`specs` are (months|None, type) pairs; None leaves `months` absent."""
    out = []
    for months, kind in specs:
        year: dict = {"amount": 1000, "type": kind}
        if months is not None:
            year["months"] = months
        out.append(year)
    return out


# ── The absent-means-twelve contract ─────────────────────────────────────────────


@pytest.mark.parametrize("raw", [None, 0, -3, "", "abc", 12])
def test_a_missing_or_unusable_months_reads_as_a_full_year(raw):
    """Every lease stored before this feature existed has no `months` at all, so the
    default is what keeps them all correct. A zero would also stall the cumulative walk,
    so it degrades the same way rather than being trusted."""
    year = {"amount": 1000, "type": "contract"}
    if raw is not None:
        year["months"] = raw
    assert period_months(year) == 12


def test_a_lease_of_whole_years_behaves_exactly_as_before():
    rows = years((None, "contract"), (None, "contract"), (None, "option"))
    assert total_months(rows) == 36
    assert schedule_end(START, rows) == date(2029, 1, 1)
    assert contract_end(START, rows) == date(2028, 1, 1)
    assert period_start(START, rows, 1) == date(2027, 1, 1)


# ── Short tails ──────────────────────────────────────────────────────────────────


def test_a_short_tail_shifts_everything_after_it():
    # "2 years and 4 months, then a 1-year option."
    rows = years((None, "contract"), (None, "contract"), (4, "contract"), (None, "option"))
    assert period_start(START, rows, 2) == date(2028, 1, 1)
    assert period_start(START, rows, 3) == date(2028, 5, 1)
    assert contract_end(START, rows) == date(2028, 5, 1)
    assert schedule_end(START, rows) == date(2029, 5, 1)


def test_an_eighteen_month_lease():
    rows = years((None, "contract"), (6, "contract"))
    assert schedule_end(START, rows) == date(2027, 7, 1)
    assert contract_end(START, rows) == date(2027, 7, 1)


def test_a_lease_shorter_than_a_year():
    rows = years((8, "contract"))
    assert schedule_end(START, rows) == date(2026, 9, 1)


def test_contract_end_ignores_trailing_options():
    """An option year is not yet exercised, so the binding term ends before it — that is
    the date the landlord has to decide something on."""
    rows = years((None, "contract"), (None, "option"), (None, "option"))
    assert contract_end(START, rows) == date(2027, 1, 1)
    assert schedule_end(START, rows) == date(2029, 1, 1)


def test_contract_end_is_none_for_an_all_option_lease():
    assert contract_end(START, years((None, "option"))) is None


# ── Which period covers a given point in time ────────────────────────────────────


@pytest.mark.parametrize(
    "month,expected",
    [
        (date(2026, 1, 1), 0),
        (date(2026, 12, 1), 0),
        (date(2027, 1, 1), 1),
        (date(2028, 1, 1), 2),  # the 4-month tail
        (date(2028, 4, 1), 2),
        (date(2028, 5, 1), 3),  # the option
        (date(2029, 5, 1), None),  # past the end
        (date(2025, 12, 1), None),  # before the start
    ],
)
def test_period_index_for_month_walks_the_durations(month, expected):
    rows = years((None, "contract"), (None, "contract"), (4, "contract"), (None, "option"))
    assert period_index_for_month(START, rows, month) == expected


def test_rent_lookup_is_month_granular_but_boundaries_are_day_granular():
    """A lease starting mid-month: rent for that whole calendar month is period one, but
    the *next* period does not actually begin until the anniversary day. The two helpers
    exist to keep that distinction, since conflating them either misprices a month or
    reprices CPI on the wrong date."""
    start = date(2026, 1, 15)
    rows = years((None, "contract"), (None, "contract"))

    assert period_index_for_month(start, rows, date(2027, 1, 1)) == 1
    assert period_index_on(start, rows, date(2027, 1, 1)) == 0
    assert period_index_on(start, rows, date(2027, 1, 15)) == 1

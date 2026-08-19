"""Where each lease period starts and ends.

A lease is stored as a list of periods (``lease_years``). Every period used to be
exactly twelve months, and that assumption was spread across the codebase as
``relativedelta(years=i)`` and ``months_elapsed // 12``. Real leases are not all whole
years — an eighteen-month term, a thirty-month term, a four-month holdover at the end —
so a period now carries its own length.

**An absent ``months`` means twelve.** That is what makes this change free: every stored
row keeps its exact shape and meaning, there is nothing to migrate, and a lease that is
all whole years behaves precisely as it did.

Only the *last* period of the contract block and of the option block can be short — the
form has no way to express anything else, because a partial period in the middle of a
lease is not a thing that happens. Nothing here depends on that, though: the walk is
cumulative, so an odd shape arriving through the API is priced correctly rather than
being silently misread.
"""
from datetime import date
from typing import Optional

from dateutil.relativedelta import relativedelta

DEFAULT_PERIOD_MONTHS = 12


def period_months(year: dict) -> int:
    """Length of one period. Absent, null or nonsensical all read as a full year, so a
    malformed blob degrades to the old behaviour rather than to a zero-length period
    that would make the cumulative walk stand still."""
    raw = year.get("months") if isinstance(year, dict) else None
    try:
        months = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PERIOD_MONTHS
    return months if months > 0 else DEFAULT_PERIOD_MONTHS


def months_before(lease_years: list[dict], index: int) -> int:
    """Total months elapsed before period ``index`` begins."""
    return sum(period_months(y) for y in lease_years[:index])


def total_months(lease_years: list[dict]) -> int:
    return sum(period_months(y) for y in lease_years)


def period_start(lease_start: date, lease_years: list[dict], index: int) -> date:
    """The date period ``index`` begins — the boundary CPI reprices against.

    Replaces ``lease_start + relativedelta(years=index)``. For an all-twelve-month lease
    the two are identical, which is why this can be swapped in everywhere without
    changing a single existing schedule.
    """
    return lease_start + relativedelta(months=months_before(lease_years, index))


def _index_from_elapsed(lease_years: list[dict], elapsed_months: int) -> Optional[int]:
    if elapsed_months < 0:
        return None
    cursor = 0
    for i, year in enumerate(lease_years):
        cursor += period_months(year)
        if elapsed_months < cursor:
            return i
    return None


def period_index_for_month(
    lease_start: Optional[date], lease_years: list[dict], month: date
) -> Optional[int]:
    """Zero-based index of the period covering ``month``, counted in whole calendar
    months.

    For *rent* lookups. Rent is charged per calendar month, so a lease starting on the
    15th still has the whole of that month priced at period one — which is what the
    clients' ``getRentForMonth`` does, and the two must agree or the overdue amount and
    the payment grid disagree about what is owed.
    """
    if lease_start is None or not lease_years:
        return None
    elapsed = (month.year - lease_start.year) * 12 + (month.month - lease_start.month)
    return _index_from_elapsed(lease_years, elapsed)


def period_index_on(
    lease_start: Optional[date], lease_years: list[dict], on: date
) -> Optional[int]:
    """Zero-based index of the period containing the *day* ``on``.

    For period *boundaries* — which period has actually begun, and therefore when CPI
    reprices. Unlike :func:`period_index_for_month` a lease starting on the 15th does not
    enter its second period until the 15th, because the repricing date is a real date the
    tenant can be held to.
    """
    if lease_start is None or not lease_years or on < lease_start:
        return None
    delta = relativedelta(on, lease_start)
    return _index_from_elapsed(lease_years, delta.years * 12 + delta.months)


def schedule_end(lease_start: date, lease_years: list[dict]) -> date:
    """When the whole signed schedule runs out, options included.

    This is the boundary the "is this renter active" queries use: an option year is still
    a year the tenant may be living there and owing rent.
    """
    return lease_start + relativedelta(months=total_months(lease_years))


def contract_end(lease_start: date, lease_years: list[dict]) -> Optional[date]:
    """When the *binding* term runs out — the last contract period's end.

    Distinct from :func:`schedule_end` on purpose. An option year is not yet exercised,
    so this is the date the landlord actually has to decide something, and therefore what
    the apps display and what the lease-expiring alert fires from. ``None`` when the
    lease is all options, which is not a real lease but does reach the API.
    """
    last_contract = -1
    for i, year in enumerate(lease_years):
        if year.get("type") != "option":
            last_contract = i
    if last_contract < 0:
        return None
    return lease_start + relativedelta(months=months_before(lease_years, last_contract + 1))

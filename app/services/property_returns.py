"""What a property returns: yields and appreciation, from what the owner has entered.

**The one place these figures are computed.** The apps display what this returns (on
``PropertyRead.returns``) and the assistant quotes it (``get_property_returns``), so the
number on screen and the number in a chat answer cannot disagree. Reports that show a yield
later must read it from here too, not recompute it.

Every figure is ``None`` when an input it needs is missing — a property with no value has no
yield, not a yield of 0% — and ``unavailable`` names why, so a reader can say what to enter
rather than guess.

The rules it reuses rather than restates:
- current rent is :func:`rent_for_month` over the tenancies :func:`is_tenancy_ended` has not
  closed — the apps' "monthly rent" tile;
- a transaction belongs to its ``EFFECTIVE_DATE`` — the month rent is *for*, the day an
  expense was paid — the rule every total in the apps uses.

It also computes the **cash flow after the mortgage** — what is left of the rent each month
once the bank and the running costs are paid — for a property with a mortgage, where the
country has the capability. Shown beside the yearly net, never instead of it.
"""
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from app.repositories.transaction_repository import TransactionRepository
from app.services import country_service
from app.services.lease_periods import is_tenancy_ended, rent_for_month

# Why a figure could not be computed. Stable codes: the assistant reads them, and a client
# may map them to copy.
NO_CURRENT_VALUE = "no_current_value"
VACANT = "vacant"
NO_RENT_AMOUNT = "no_rent_amount"
NO_RECENT_RENT = "no_recent_rent"
NO_PURCHASE_PRICE = "no_purchase_price"
NO_PURCHASE_DATE = "no_purchase_date"
HELD_UNDER_A_YEAR = "held_under_a_year"
NO_ACTIVE_MORTGAGE = "no_active_mortgage"

_DAYS_PER_YEAR = 365.25


@dataclass
class PropertyReturns:
    # Current monthly rent × 12 ÷ current value.
    gross_yield: Optional[float] = None
    # (Revenue − expenses in the window) ÷ current value.
    net_yield: Optional[float] = None
    # Current value − purchase price, and that as a share of the price.
    appreciation: Optional[float] = None
    appreciation_pct: Optional[float] = None
    # Compound yearly growth, purchase date → the current value's date. None under a year:
    # annualising a few months' change produces a number nobody should act on.
    appreciation_per_year: Optional[float] = None
    # The inputs behind the yields, so a reader can say what a figure is based on.
    annual_rent: Optional[float] = None
    net_income_12m: Optional[float] = None
    # The trailing twelve months the net yield covers: after window_start, up to window_end.
    window_start: Optional[date] = None
    window_end: Optional[date] = None
    # Cash flow after the mortgage, monthly. Only for a property with a mortgage.
    # The mortgage payments in force today (0 once every one has ended).
    monthly_mortgage: Optional[float] = None
    # Expenses over the trailing twelve months ÷ 12: running costs vary month to month.
    monthly_expenses: Optional[float] = None
    # Current monthly rent − monthly_mortgage − monthly_expenses.
    monthly_left: Optional[float] = None
    # Current monthly rent ÷ monthly_mortgage. Under 1.0 the rent does not cover the bank.
    mortgage_coverage: Optional[float] = None
    # figure name → reason code, for every figure above that is None.
    unavailable: dict[str, str] = field(default_factory=dict)


def _lease_years(raw) -> list[dict]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return raw if isinstance(raw, list) else []


def current_monthly_rent(renters: Iterable, today: date) -> Optional[float]:
    """The rent the property earns now: every tenancy that has not ended, at this month's
    amount. None when the property is vacant — distinct from 0, a tenancy with no amount."""
    current = [r for r in renters if not is_tenancy_ended(r.terminated_on, r.lease_end, today)]
    if not current:
        return None
    month = today.replace(day=1)
    return sum(rent_for_month(_lease_years(r.lease_years), r.lease_start, month) for r in current)


def twelve_month_window(today: date) -> tuple[date, date]:
    """(exclusive start, inclusive end) — a full year in January too, unlike the calendar
    year the property page's totals cover."""
    return today - relativedelta(years=1), today


def _cash_flow(
    r: PropertyReturns,
    property,
    monthly_rent: Optional[float],
    expenses_12m: Decimal,
    today: date,
    mortgage_enabled: bool,
) -> None:
    """Fill the cash-flow-after-mortgage figures on `r`.

    Nothing at all — not even an ``unavailable`` entry — for a property without a mortgage
    or where the country has none: most properties have no mortgage, and that is not a gap
    in what the owner entered. Its screen shows the invitation to add one instead, and the
    yearly net it always had. A vacant property has no rent to leave anything of, so "left"
    and coverage are withheld rather than shown negative.
    """
    mortgages = getattr(property, "mortgages", None) or []
    if not mortgage_enabled or not mortgages:
        return
    r.monthly_mortgage = sum(m.payment_on(today) or 0 for m in mortgages)
    r.monthly_expenses = float(expenses_12m) / 12
    if monthly_rent is None or monthly_rent <= 0:
        reason = VACANT if monthly_rent is None else NO_RENT_AMOUNT
        r.unavailable["monthly_left"] = reason
        r.unavailable["mortgage_coverage"] = reason
        return
    r.monthly_left = monthly_rent - r.monthly_mortgage - r.monthly_expenses
    if r.monthly_mortgage <= 0:
        r.unavailable["mortgage_coverage"] = NO_ACTIVE_MORTGAGE
    else:
        r.mortgage_coverage = monthly_rent / r.monthly_mortgage


def compute_returns(
    property,
    monthly_rent: Optional[float],
    revenue_12m: Decimal,
    expenses_12m: Decimal,
    today: date,
    mortgage_enabled: bool = False,
) -> PropertyReturns:
    """The figures for one property. Pure: everything it needs is passed in."""
    start, end = twelve_month_window(today)
    r = PropertyReturns(window_start=start, window_end=end)
    _cash_flow(r, property, monthly_rent, expenses_12m, today, mortgage_enabled)
    value = property.current_value if property.current_value and property.current_value > 0 else None
    price = property.purchase_price if property.purchase_price and property.purchase_price > 0 else None

    # Gross yield.
    if monthly_rent is not None:
        r.annual_rent = monthly_rent * 12
    if value is None:
        r.unavailable["gross_yield"] = NO_CURRENT_VALUE
    elif monthly_rent is None:
        r.unavailable["gross_yield"] = VACANT
    elif monthly_rent <= 0:
        r.unavailable["gross_yield"] = NO_RENT_AMOUNT
    else:
        r.gross_yield = r.annual_rent / value

    # Net yield. No rent recorded in the window means nothing to measure — not a 0% or a
    # negative yield.
    if revenue_12m > 0:
        r.net_income_12m = float(revenue_12m - expenses_12m)
    if value is None:
        r.unavailable["net_yield"] = NO_CURRENT_VALUE
    elif revenue_12m <= 0:
        r.unavailable["net_yield"] = NO_RECENT_RENT
    else:
        r.net_yield = r.net_income_12m / value

    # Appreciation.
    if value is None or price is None:
        reason = NO_CURRENT_VALUE if value is None else NO_PURCHASE_PRICE
        for name in ("appreciation", "appreciation_pct", "appreciation_per_year"):
            r.unavailable[name] = reason
        return r
    r.appreciation = value - price
    r.appreciation_pct = r.appreciation / price
    if property.purchase_date is None:
        r.unavailable["appreciation_per_year"] = NO_PURCHASE_DATE
        return r
    until = property.current_value_date or today
    years = (until - property.purchase_date).days / _DAYS_PER_YEAR
    if years < 1:
        r.unavailable["appreciation_per_year"] = HELD_UNDER_A_YEAR
    else:
        r.appreciation_per_year = (value / price) ** (1 / years) - 1
    return r


def returns_for_properties(
    session: Session, owner_id: str, properties: list, today: date
) -> dict[int, PropertyReturns]:
    """{property_id: returns} for a list of properties — one transactions query, whatever
    the length of the list. The properties must have `renters` loaded."""
    start, end = twelve_month_window(today)
    totals = TransactionRepository(session).totals_by_property(
        owner_id, [p.id for p in properties], start, end
    )
    zero = (Decimal("0"), Decimal("0"))
    return {
        p.id: compute_returns(
            p,
            current_monthly_rent(p.renters or [], today),
            *totals.get(p.id, zero),
            today=today,
            mortgage_enabled=country_service.capabilities_for(p.country).mortgage,
        )
        for p in properties
    }

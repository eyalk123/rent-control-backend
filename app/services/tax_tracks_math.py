"""The arithmetic of the tax tracks report: brackets, credit points, surtax, the exemption's
reduced ceiling. Pure functions over the year's figures, so each rule can be tested alone.

Every rate and threshold comes in as an argument, read from ``tax_values``. None is written
here. Bracket tables are the booklet's annual ones: ``[[upper, rate_pct], ..., [None, top]]``.
"""
import math


def bracket_tax(brackets: list, start: float, amount: float) -> float:
    """Tax on ``amount`` of income placed on top of ``start`` already earned."""
    if amount <= 0:
        return 0.0
    low, high = start, start + amount
    tax, lower = 0.0, 0.0
    for upper, rate in brackets:
        top = math.inf if upper is None else upper
        overlap = min(high, top) - max(low, lower)
        if overlap > 0:
            tax += overlap * rate / 100
        if top >= high:
            break
        lower = top
    return tax


def income_for_tax(brackets: list, tax: float) -> float:
    """The income whose tax on ``brackets`` is ``tax``, the inverse of :func:`bracket_tax`
    from zero. Used to find the income at which the basic credit points are used up."""
    lower, paid = 0.0, 0.0
    for upper, rate in brackets:
        if rate <= 0:
            lower = upper if upper is not None else lower
            continue
        span_tax = math.inf if upper is None else (upper - lower) * rate / 100
        if paid + span_tax >= tax:
            return lower + (tax - paid) * 100 / rate
        paid += span_tax
        lower = upper
    return lower


def income_tax_on_rent(
    personal: list, rent_table: list, other_income: float, rent_income: float, credits: float
) -> float:
    """How much the rent adds to the year's income tax.

    Other income is taxed on the personal-exertion brackets; the rent sits on top of it, on
    ``rent_table`` — the brackets from 31% for rent, which is not personal exertion, or the
    personal ones from the year the owner turns 60. Credit points come off the total, so
    they help the rent only where the other income has not used them up.
    """
    on_other = bracket_tax(personal, 0, other_income)
    total = on_other + bracket_tax(rent_table, other_income, rent_income)
    return max(0.0, total - credits) - max(0.0, on_other - credits)


def surtax_on_rent(
    threshold: float,
    rate_pct: float,
    capital_rate_pct: float | None,
    other_income: float,
    rent_income: float,
) -> tuple[float, float]:
    """The surtax the rent adds, as ``(general, capital)``.

    General (section 121B(a)): ``rate_pct`` of all taxable income above the threshold, so
    only what the rent pushes over it. Capital, from 2025 (121B(a1)): ``capital_rate_pct``
    of the income from capital sources above the same threshold, measured on that income
    alone — non-business rent is one, and other income is taken to be work or a pension,
    which is not (Tax Authority circular 5/2025, example 3.3).
    """
    general = rate_pct / 100 * (
        max(0.0, other_income + rent_income - threshold) - max(0.0, other_income - threshold)
    )
    capital = (capital_rate_pct or 0) / 100 * max(0.0, rent_income - threshold)
    return general, capital


def exempt_amount(track_rent: float, all_rent: float, ceiling: float) -> float:
    """The month's exempt rent for the flats on the exemption track.

    The ceiling is reduced by however much the rent from every residential flat — on any
    track — exceeds it, and the exemption covers the track's rent up to what is left. With
    every flat on the track this is the familiar rule: taxable = 2 × (rent − ceiling).
    """
    return min(track_rent, max(0.0, 2 * ceiling - all_rent))

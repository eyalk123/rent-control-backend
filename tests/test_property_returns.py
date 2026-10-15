"""Yields and appreciation: the calculation, the API field, and the assistant's tool.

The figures are pinned to the same worked examples the apps were checked against, so a
change to the formula shows up here rather than as two screens quoting different numbers.
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from dateutil.relativedelta import relativedelta

from app.models.property_valuation import PropertyValuation
from app.models.transaction import TransactionTypeEnum
from app.services.agent_tools import AgentTools
from app.services.property_returns import (
    HELD_UNDER_A_YEAR,
    NO_CURRENT_VALUE,
    NO_PURCHASE_DATE,
    NO_PURCHASE_PRICE,
    NO_RECENT_RENT,
    VACANT,
    compute_returns,
)
from tests.conftest import OWNER_A
from tests.factories import make_property, make_renter, make_transaction

TODAY = date(2026, 10, 14)


def _prop(**kw):
    defaults = dict(
        current_value=2_400_000.0,
        current_value_date=TODAY,
        purchase_price=1_800_000.0,
        purchase_date=date(2023, 10, 14),
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# --- the calculation --------------------------------------------------------------------


def test_worked_example():
    r = compute_returns(_prop(), 1950.0, Decimal("60000"), Decimal("12000"), TODAY)
    assert r.gross_yield == pytest.approx(1950 * 12 / 2_400_000)  # 0.975%
    assert r.annual_rent == 23_400
    assert r.net_income_12m == 48_000
    assert r.net_yield == pytest.approx(48_000 / 2_400_000)  # 2.0%
    assert r.appreciation == 600_000
    assert r.appreciation_pct == pytest.approx(1 / 3)
    assert r.appreciation_per_year == pytest.approx((2.4 / 1.8) ** (1 / 3) - 1, abs=1e-3)  # 10.1%
    assert r.unavailable == {}
    assert r.window_start == date(2025, 10, 14)
    assert r.window_end == TODAY


def test_no_value_means_no_yields_and_says_why():
    r = compute_returns(_prop(current_value=None), 1950.0, Decimal("60000"), Decimal("0"), TODAY)
    assert r.gross_yield is None and r.net_yield is None and r.appreciation is None
    assert r.unavailable["gross_yield"] == NO_CURRENT_VALUE
    assert r.unavailable["net_yield"] == NO_CURRENT_VALUE
    assert r.unavailable["appreciation"] == NO_CURRENT_VALUE


def test_vacant_property_has_no_gross_yield_not_zero():
    r = compute_returns(_prop(), None, Decimal("0"), Decimal("0"), TODAY)
    assert r.gross_yield is None
    assert r.unavailable["gross_yield"] == VACANT


def test_no_rent_in_window_means_no_net_yield_even_with_expenses():
    r = compute_returns(_prop(), 1950.0, Decimal("0"), Decimal("5000"), TODAY)
    assert r.net_yield is None
    assert r.unavailable["net_yield"] == NO_RECENT_RENT


def test_appreciation_needs_price_and_per_year_needs_a_year():
    r = compute_returns(_prop(purchase_price=None), 1950.0, Decimal("0"), Decimal("0"), TODAY)
    assert r.unavailable["appreciation"] == NO_PURCHASE_PRICE

    r = compute_returns(_prop(purchase_date=None), 1950.0, Decimal("0"), Decimal("0"), TODAY)
    assert r.appreciation == 600_000
    assert r.unavailable["appreciation_per_year"] == NO_PURCHASE_DATE

    r = compute_returns(
        _prop(purchase_date=TODAY - timedelta(days=200)), 1950.0, Decimal("0"), Decimal("0"), TODAY
    )
    assert r.appreciation_pct == pytest.approx(1 / 3)
    assert r.appreciation_per_year is None
    assert r.unavailable["appreciation_per_year"] == HELD_UNDER_A_YEAR


# --- the API field ------------------------------------------------------------------------


def _valued_property(db_session, value=2_400_000.0):
    prop = make_property(db_session, purchase_price=1_800_000.0, purchase_date=date(2020, 1, 1))
    db_session.add(PropertyValuation(property_id=prop.id, value=value, valued_on=date.today()))
    db_session.commit()
    return prop


def test_detail_and_list_carry_server_computed_returns(client, db_session):
    prop = _valued_property(db_session)
    start = date.today() - relativedelta(months=6)
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=start,
        lease_years=[{"amount": 2000.0, "type": "contract"}],
    )
    this_month = date.today().replace(day=1)
    make_transaction(db_session, property_id=prop.id, amount=6000, month_for=this_month)
    make_transaction(
        db_session, property_id=prop.id, amount=1200, type=TransactionTypeEnum.EXPENSE,
        date_of_payment=date.today(),
    )
    # Outside the twelve months: ignored.
    make_transaction(
        db_session, property_id=prop.id, amount=99_999,
        month_for=this_month - relativedelta(years=2),
        date_of_payment=date.today() - relativedelta(years=2),
    )

    detail = client.get(f"/properties/{prop.id}").json()["returns"]
    assert detail["gross_yield"] == pytest.approx(2000 * 12 / 2_400_000)
    assert detail["net_income_12m"] == 4800
    assert detail["net_yield"] == pytest.approx(4800 / 2_400_000)
    assert detail["appreciation"] == 600_000
    assert detail["unavailable"] == {}

    listed = next(p for p in client.get("/properties").json() if p["id"] == prop.id)
    assert listed["returns"] == detail


def test_unvalued_property_reports_why(client, db_session):
    prop = make_property(db_session)
    returns = client.get(f"/properties/{prop.id}").json()["returns"]
    assert returns["gross_yield"] is None
    assert returns["unavailable"]["gross_yield"] == NO_CURRENT_VALUE


# --- the assistant ------------------------------------------------------------------------


def test_assistant_tool_quotes_the_same_figures(client, db_session):
    prop = _valued_property(db_session)
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=date.today() - relativedelta(months=6),
        lease_years=[{"amount": 2000.0, "type": "contract"}],
    )
    api = client.get(f"/properties/{prop.id}").json()["returns"]

    one = AgentTools(db_session).dispatch("get_property_returns", OWNER_A, {"property_id": prop.id})
    assert one["gross_yield"] == pytest.approx(api["gross_yield"])
    assert one["gross_yield_display"] == "1.0%"
    assert one["appreciation_pct_display"] == "+33.3%"
    assert one["unavailable"]["net_yield"] == NO_RECENT_RENT

    every = AgentTools(db_session).dispatch("get_property_returns", OWNER_A, {})
    assert every["count"] == 1
    assert every["properties"][0]["property_id"] == prop.id


def test_assistant_tool_unknown_property(db_session):
    out = AgentTools(db_session).dispatch("get_property_returns", OWNER_A, {"property_id": 999})
    assert out == {"error": "not found"}


def test_aggregate_sums_current_value_skipping_unvalued(db_session):
    _valued_property(db_session, value=1_000_000.0)
    _valued_property(db_session, value=500_000.0)
    make_property(db_session)  # no value: must not count as 0 nor break the sum
    out = AgentTools(db_session).dispatch(
        "aggregate", OWNER_A, {"entity": "properties", "operation": "sum", "value_field": "current_value"}
    )
    assert out["value"] == 1_500_000

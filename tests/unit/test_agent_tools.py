"""Unit tests for the agent tool layer (real repos/services over the test session).

These assert two things at once: the tools return correct, service-backed numbers,
and they are strictly owner-scoped — a tool run as OWNER_A can never surface OWNER_B's
data, even when handed OWNER_B's ids.
"""
from datetime import date, timedelta

from app.services.agent_tools import AgentTools, format_shekels
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import (
    make_expense_category,
    make_property,
    make_renter,
    make_supplier,
    make_transaction,
)

_TODAY = date.today()
_ACTIVE_LEASE = dict(lease_start=_TODAY - timedelta(days=30), lease_end=_TODAY + timedelta(days=335))


def test_format_shekels():
    assert format_shekels(12000) == "₪12,000"
    assert format_shekels(12000.5) == "₪12,000.50"
    assert format_shekels(None) == "₪0"


def test_list_properties_reports_occupancy_and_current_renter(db_session):
    prop = make_property(db_session, address="HaPalmach 12", city="Haifa", property_owner="Dad")
    make_renter(
        db_session, property_id=prop.id, first_name="Yossi", last_name="Cohen", **_ACTIVE_LEASE
    )
    vacant = make_property(db_session, address="Empty St 1", city="Eilat")

    result = AgentTools(db_session).dispatch("list_properties", OWNER_A, {})

    assert result["count"] == 2
    by_id = {p["id"]: p for p in result["properties"]}
    occupied = by_id[prop.id]
    assert occupied["status"] == "occupied"
    assert occupied["property_owner"] == "Dad"
    assert occupied["current_renter"]["name"] == "Yossi Cohen"
    assert occupied["current_renter"]["monthly_rent"] == 12000.0
    assert occupied["current_renter"]["monthly_rent_display"] == "₪12,000"
    assert by_id[vacant.id]["status"] == "vacant"
    assert by_id[vacant.id]["current_renter"] is None


def test_list_properties_occupied_matches_app_hasrenters(db_session):
    """Occupied = the property has ANY linked renter (the app's hasRenters), even if that
    renter's lease has ended — the old active-lease-window rule under-counted vs. the UI."""
    prop = make_property(db_session)
    make_renter(db_session, property_id=prop.id, lease_start=date(2020, 1, 1), lease_end=date(2021, 1, 1))
    make_property(db_session)  # genuinely empty

    result = AgentTools(db_session).dispatch("list_properties", OWNER_A, {})
    by_id = {p["id"]: p for p in result["properties"]}
    assert by_id[prop.id]["status"] == "occupied"  # 'vacant' under the old active-only rule
    assert result["occupied_count"] == 1
    assert result["vacant_count"] == 1


def test_aggregate_count_occupied_properties_with_lease_to_2027(db_session):
    """The motivating example: an arbitrary filter+count the model must not tally itself."""
    a = make_property(db_session, city="Haifa")
    make_renter(db_session, property_id=a.id, lease_start=date(2025, 1, 1), lease_end=date(2027, 6, 1))
    b = make_property(db_session, city="Eilat")
    make_renter(db_session, property_id=b.id, lease_start=date(2024, 1, 1), lease_end=date(2025, 1, 1))
    make_property(db_session)  # vacant

    res = AgentTools(db_session).dispatch(
        "aggregate",
        OWNER_A,
        {
            "entity": "properties",
            "operation": "count",
            "filters": {"occupied": True, "current_lease_end": {"gte": "2027-01-01"}},
        },
    )
    assert res["matched"] == 1 and res["value"] == 1
    assert res["ids"] == [a.id]


def test_aggregate_sum_and_group_by(db_session):
    prop = make_property(db_session)
    make_transaction(db_session, type="expense", property_id=prop.id, amount=300.0)
    make_transaction(db_session, type="expense", property_id=prop.id, amount=700.0)
    make_transaction(db_session, type="revenue", property_id=prop.id, amount=5000.0)
    tools = AgentTools(db_session)

    total = tools.dispatch(
        "aggregate",
        OWNER_A,
        {"entity": "transactions", "operation": "sum", "value_field": "amount", "filters": {"type": "expense"}},
    )
    assert total["value"] == 1000.0
    assert total["value_display"] == "₪1,000"

    grouped = tools.dispatch(
        "aggregate",
        OWNER_A,
        {"entity": "transactions", "operation": "sum", "value_field": "amount", "group_by": "type"},
    )
    by_type = {g["key"]: g["value"] for g in grouped["groups"]}
    assert by_type["expense"] == 1000.0 and by_type["revenue"] == 5000.0


def test_aggregate_unknown_field_errors(db_session):
    res = AgentTools(db_session).dispatch(
        "aggregate", OWNER_A, {"entity": "renters", "operation": "count", "filters": {"favorite_color": "blue"}}
    )
    assert "error" in res and "favorite_color" in res["error"]


def test_aggregate_is_owner_scoped(db_session):
    prop = make_property(db_session, owner_id=OWNER_B)
    make_renter(db_session, owner_id=OWNER_B, property_id=prop.id, **_ACTIVE_LEASE)
    make_transaction(db_session, owner_id=OWNER_B, type="expense", property_id=prop.id, amount=999.0)
    tools = AgentTools(db_session)
    assert tools.dispatch("aggregate", OWNER_A, {"entity": "properties", "operation": "count"})["matched"] == 0
    assert (
        tools.dispatch(
            "aggregate", OWNER_A, {"entity": "transactions", "operation": "sum", "value_field": "amount"}
        )["value"]
        == 0
    )


def test_query_transactions_totals_and_filters(db_session):
    prop = make_property(db_session)
    make_transaction(db_session, type="expense", property_id=prop.id, amount=300.0, notes="fix tap")
    make_transaction(db_session, type="expense", property_id=prop.id, amount=700.0, notes="paint")
    make_transaction(db_session, type="revenue", property_id=prop.id, amount=5000.0)

    tools = AgentTools(db_session)
    expenses = tools.dispatch("query_transactions", OWNER_A, {"type": "expense"})

    assert expenses["count"] == 2
    assert expenses["total"] == 1000.0
    assert expenses["total_display"] == "₪1,000"
    assert expenses["truncated"] is False

    revenue = tools.dispatch("query_transactions", OWNER_A, {"type": "revenue"})
    assert revenue["count"] == 1
    assert revenue["total"] == 5000.0


def test_query_transactions_bad_date_returns_error_not_crash(db_session):
    result = AgentTools(db_session).dispatch(
        "query_transactions", OWNER_A, {"from_date": "not-a-date"}
    )
    assert "error" in result


def test_unknown_tool_is_refused(db_session):
    result = AgentTools(db_session).dispatch("drop_all_tables", OWNER_A, {})
    assert result == {"error": "unknown tool: drop_all_tables"}


def test_tools_are_owner_scoped(db_session):
    """OWNER_B's ids must yield nothing when the tools run as OWNER_A."""
    prop_b = make_property(db_session, owner_id=OWNER_B, address="Secret 1", city="Tel Aviv")
    make_renter(db_session, owner_id=OWNER_B, property_id=prop_b.id, **_ACTIVE_LEASE)
    make_transaction(db_session, owner_id=OWNER_B, type="expense", property_id=prop_b.id, amount=999.0)

    tools = AgentTools(db_session)

    # A sees an empty portfolio...
    assert tools.dispatch("list_properties", OWNER_A, {})["count"] == 0

    # ...and even when A explicitly targets B's property id, the total is zero.
    scoped = tools.dispatch("query_transactions", OWNER_A, {"property_id": prop_b.id})
    assert scoped["count"] == 0
    assert scoped["total"] == 0.0

    # An injected owner_id in the tool input is ignored (owner stays A).
    injected = tools.dispatch("list_properties", OWNER_A, {"owner_id": OWNER_B})
    assert injected["count"] == 0


def test_get_property_headline_numbers(db_session):
    prop = make_property(db_session, address="HaPalmach 12", city="Haifa")
    make_renter(db_session, property_id=prop.id, first_name="Yossi", last_name="Cohen", **_ACTIVE_LEASE)
    make_transaction(db_session, type="revenue", property_id=prop.id, amount=5000.0)
    make_transaction(db_session, type="revenue", property_id=prop.id, amount=5000.0)
    make_transaction(db_session, type="expense", property_id=prop.id, amount=1200.0)

    result = AgentTools(db_session).dispatch("get_property", OWNER_A, {"property_id": prop.id})

    assert result["address"] == "HaPalmach 12"
    assert result["status"] == "occupied"
    assert result["current_renter"]["name"] == "Yossi Cohen"
    assert result["totals"]["revenue"] == 10000.0
    assert result["totals"]["expenses"] == 1200.0
    assert result["totals"]["net"] == 8800.0
    assert result["totals"]["net_display"] == "₪8,800"


def test_list_renters_reports_lease_terms(db_session):
    prop = make_property(db_session, address="Herzl 1", city="Tel Aviv")
    make_renter(
        db_session,
        property_id=prop.id,
        first_name="Dana",
        last_name="Levi",
        contract_term_years=2,
        option_years=1,
        number_of_payments=12,
        payment_day_of_month=5,
        **_ACTIVE_LEASE,
    )

    result = AgentTools(db_session).dispatch("list_renters", OWNER_A, {})

    assert result["count"] == 1
    r = result["renters"][0]
    assert r["name"] == "Dana Levi"
    assert r["property_address"] == "Herzl 1, Tel Aviv"
    assert r["contract_term_years"] == 2
    assert r["option_years"] == 1
    assert r["payment_frequency"] == "monthly"
    assert r["current_monthly_rent"] == 12000.0


def test_get_lease_schedule_year_by_year(db_session):
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        lease_years=[
            {"amount": 5000.0, "type": "contract"},
            {"amount": 5200.0, "type": "contract"},
            {"amount": 5400.0, "type": "option"},
        ],
        lease_start=date(2026, 1, 1),
    )

    result = AgentTools(db_session).dispatch(
        "get_lease_schedule", OWNER_A, {"renter_id": renter.id}
    )

    assert len(result["years"]) == 3
    assert result["years"][0]["amount_display"] == "₪5,000"
    assert result["years"][2]["type"] == "option"
    assert result["years"][1]["starts"] == "2027-01-01"
    assert result["cpi_linked"] is False


def test_get_lease_schedule_cross_owner_returns_error(db_session):
    prop = make_property(db_session, owner_id=OWNER_B)
    renter = make_renter(db_session, owner_id=OWNER_B, property_id=prop.id)
    result = AgentTools(db_session).dispatch(
        "get_lease_schedule", OWNER_A, {"renter_id": renter.id}
    )
    assert result == {"error": "not found"}


def _seed_cpi_lease(db_session):
    """A 4-year CPI lease starting 2024-01-01, base index 100. Index readings are
    seeded so years 1-3 are finalized and year 4 (anniversary 2027) is a projection.
    (Today is 2026-07 per the test environment.)"""
    from app.repositories.cpi_index_repository import CpiIndexRepository

    CpiIndexRepository(db_session).upsert_many(
        120010,
        [
            (2023, 11, 100.0),  # known index for the 2024-01 anniversary (year 1)
            (2024, 11, 105.0),  # year 2
            (2025, 11, 110.0),  # year 3
            (2026, 6, 112.0),   # latest known now — stands in for the future year 4
        ],
    )
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        first_name="Rina",
        last_name="Katz",
        rent_escalation_mode="cpi",
        lease_start=date(2024, 1, 1),
        base_rent=5000.0,
        cpi_base_index=100.0,
        lease_years=[{"amount": 5000.0, "type": "contract"} for _ in range(4)],
    )
    return renter


def test_explain_cpi_matches_engine_finalized_and_projected(db_session):
    from app.repositories.cpi_index_repository import CpiIndexRepository
    from app.services.cpi_indexing_service import materialize_cpi_amounts

    renter = _seed_cpi_lease(db_session)
    tools = AgentTools(db_session)

    # The real engine path the app uses on create/update — the source of truth.
    repo = CpiIndexRepository(db_session)
    lookup = lambda d: repo.latest_on_or_before(120010, d)  # noqa: E731
    engine_years = materialize_cpi_amounts(
        [{"amount": 5000.0, "type": "contract"} for _ in range(4)],
        date(2024, 1, 1),
        base_rent=5000.0,
        base_index=100.0,
        index_lookup=lookup,
    )

    for n in (1, 2, 3, 4):
        got = tools.dispatch("explain_cpi", OWNER_A, {"renter_id": renter.id, "year": n})
        assert got["cpi_linked"] is True
        assert got["linkage"] == "whole_lease_fixed_base"
        # Every amount equals the engine's amount for that year — the model gets the same number.
        assert got["amount"] == engine_years[n - 1]["amount"]

    # Year 2: +5% since signing, finalized.
    y2 = tools.dispatch("explain_cpi", OWNER_A, {"renter_id": renter.id, "year": 2})
    assert y2["base_index"] == {"value": 100.0, "month": "2023-11"}
    assert y2["known_index"] == {"value": 105.0, "month": "2024-11"}
    assert y2["ratio"] == 1.05
    assert y2["amount"] == 5250.0
    assert y2["known_index_status"] == "finalized"
    assert y2["floor_applied"] is False

    # Year 4: anniversary is in the future, so no exact known index yet → projection.
    y4 = tools.dispatch("explain_cpi", OWNER_A, {"renter_id": renter.id, "year": 4})
    assert y4["known_index_status"] == "projected"

    # Default (no year) explains the year in effect today (year 3).
    now = tools.dispatch("explain_cpi", OWNER_A, {"renter_id": renter.id})
    assert now["year_number"] == 3


def test_explain_cpi_floor_applies_on_deflation(db_session):
    from app.repositories.cpi_index_repository import CpiIndexRepository

    CpiIndexRepository(db_session).upsert_many(
        120010, [(2023, 11, 100.0), (2024, 11, 92.0)]  # index fell for year 2
    )
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        rent_escalation_mode="cpi",
        lease_start=date(2024, 1, 1),
        base_rent=5000.0,
        cpi_base_index=100.0,
        lease_years=[{"amount": 5000.0, "type": "contract"}, {"amount": 5000.0, "type": "contract"}],
    )

    y2 = AgentTools(db_session).dispatch("explain_cpi", OWNER_A, {"renter_id": renter.id, "year": 2})
    assert y2["floor_applied"] is True
    assert y2["amount"] == 5000.0  # never below base rent (לא יפחת)


def test_explain_cpi_on_non_cpi_lease_says_so(db_session):
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        rent_escalation_mode="percent",
        lease_start=date(2025, 1, 1),
        base_rent=5000.0,
        lease_years=[{"amount": 5000.0, "type": "contract"}],
    )
    result = AgentTools(db_session).dispatch("explain_cpi", OWNER_A, {"renter_id": renter.id})
    assert result["cpi_linked"] is False
    assert "not CPI-linked" in result["message"]


def test_get_overdue_matches_needs_attention(db_session):
    """The tool must return exactly what renter_service.get_overdue_this_month returns —
    the same inference the app's Needs Attention uses."""
    from app.repositories.cpi_index_repository import CpiIndexRepository
    from app.repositories.property_repository import PropertyRepository
    from app.repositories.renter_repository import RenterRepository
    from app.services.renter_service import RenterService

    prop = make_property(db_session, property_owner="Dad")
    make_renter(
        db_session,
        property_id=prop.id,
        first_name="Late",
        last_name="Payer",
        payment_day_of_month=1,
        **_ACTIVE_LEASE,
    )

    svc = RenterService(
        RenterRepository(db_session), PropertyRepository(db_session), CpiIndexRepository(db_session)
    )
    expected = svc.get_overdue_this_month(OWNER_A)

    result = AgentTools(db_session).dispatch("get_overdue", OWNER_A, {})
    assert result["count"] == len(expected)
    if expected:
        assert result["renters"][0]["renter_id"] == expected[0].renter_id
        assert result["renters"][0]["amount_owed"] == float(expected[0].monthly_amount)


def test_get_report_summary_expense_log_repairs_by_owner(db_session):
    """'How much did I spend on repairs across Dad's properties?' — a direct lookup,
    no model arithmetic: the report gives owner-level category totals."""
    repairs = make_expense_category(db_session, name="Repairs", key="repairs")
    dad_prop = make_property(db_session, address="Dad St 1", city="Haifa", property_owner="Dad")
    other_prop = make_property(db_session, address="Mine St 2", city="Eilat", property_owner="Me")
    y = date.today().year
    make_transaction(
        db_session, type="expense", property_id=dad_prop.id, amount=300.0,
        date_of_payment=date(y, 3, 1), categories=[repairs],
    )
    make_transaction(
        db_session, type="expense", property_id=dad_prop.id, amount=700.0,
        date_of_payment=date(y, 6, 1), categories=[repairs],
    )
    make_transaction(
        db_session, type="expense", property_id=other_prop.id, amount=999.0,
        date_of_payment=date(y, 6, 1), categories=[repairs],
    )

    result = AgentTools(db_session).dispatch(
        "get_report_summary", OWNER_A, {"type": "expense_log", "year": y}
    )

    dad = next(o for o in result["owners"] if o["owner_name"] == "Dad")
    assert dad["categories"]["repairs"] == "₪1,000"  # 300 + 700, not Me's 999


def test_get_report_summary_income_expense_net(db_session):
    prop = make_property(db_session, property_owner="Dad")
    y = date.today().year
    make_transaction(db_session, type="revenue", property_id=prop.id, amount=5000.0, month_for=date(y, 4, 1))
    make_transaction(db_session, type="expense", property_id=prop.id, amount=1500.0, date_of_payment=date(y, 4, 10))

    result = AgentTools(db_session).dispatch(
        "get_report_summary", OWNER_A, {"type": "income_expense", "year": y}
    )
    assert result["grand_total"]["revenue"] == 5000.0
    assert result["grand_total"]["net"] == 3500.0


def test_list_suppliers(db_session):
    cat = make_expense_category(db_session, name="Plumbing", key="plumbing")
    make_supplier(db_session, name="Acme Plumbing", categories=[cat])
    make_supplier(db_session, name="Gardeners Inc", is_active=False)

    tools = AgentTools(db_session)
    active = tools.dispatch("list_suppliers", OWNER_A, {})
    assert active["count"] == 1
    assert active["suppliers"][0]["name"] == "Acme Plumbing"

    withinactive = tools.dispatch("list_suppliers", OWNER_A, {"include_inactive": True})
    assert withinactive["count"] == 2

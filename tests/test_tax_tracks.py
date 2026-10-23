"""Full-stack tests for /tax-tracks: the three tracks, the per-flat mix, the rent checks, and
who a picked household's rent is added to."""
from datetime import date, datetime

import pytest
from freezegun import freeze_time

from app.models.cpi_index import CpiIndex
from app.models.owner import Owner
from app.models.property import PropertyTypeEnum
from app.models.property_mortgage import MortgagePayment, MortgageYear, PropertyMortgage
from app.models.tax_year_input import TaxYearInput
from app.models.transaction import TransactionTypeEnum
from app.repositories.tax_value_repository import TaxValueRepository
from app.services import il_tax_source
from tests.conftest import OWNER_A
from tests.factories import (
    make_expense_category,
    make_property,
    make_property_owner,
    make_renter,
    make_transaction,
)

CEILING = 5654
# The 2025 booklet's figures (tests/unit/test_il_tax_source.py reads them from its text).
BOOKLET = {
    "rent_exemption_ceiling_monthly": CEILING,
    "personal_brackets": [
        [84120, 10], [120720, 14], [228000, 20], [301200, 31], [560280, 35], [None, 47]
    ],
    "other_income_brackets": [[301200, 31], [560280, 35], [None, 47]],
    "surtax_threshold": 721560,
    "credit_point_monthly": 242,
}


@pytest.fixture(autouse=True)
def _today():
    with freeze_time("2026-10-19 09:00"):
        yield


def _figures(db_session, year=2025):
    repo = TaxValueRepository(db_session)
    repo.save_document(
        "IL", year, f"http://gov/{year}.pdf", "a" * 64, BOOKLET, datetime(2026, 1, 5),
    )
    repo.sync_law_values("IL", year, il_tax_source.law_values(year))


def _lease(db_session, prop, rent, start=date(2024, 1, 1), **kw):
    return make_renter(
        db_session, property_id=prop.id, lease_start=start,
        lease_years=[{"amount": rent, "type": "contract"}] * 3, **kw,
    )


def _pay(db_session, renter, amount, year, months):
    for m in months:
        make_transaction(
            db_session, property_id=renter.property_id, renter_id=renter.id, amount=amount,
            month_for=date(year, m, 1), date_of_payment=date(year, m, 1),
        )


def _other_income(client, person_key, amount, year=2025, points=None):
    resp = client.put("/tax-tracks/inputs", json={
        "person_key": person_key, "year": year, "other_income": amount, "credit_points": points,
    })
    assert resp.status_code == 200, resp.text


def _report(client, year=2025, ids=()):
    params = {"year": year, "property_ids": ",".join(str(i) for i in ids)}
    resp = client.get("/tax-tracks/report", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _pickable(body):
    return [
        p["id"] for person in body["people"] if not person["is_company"]
        for p in person["properties"] if not p["commercial"]
    ]


def _result(client, year=2025):
    """The report with every flat there is to pick, picked."""
    return _report(client, year, _pickable(_report(client, year)))["result"]


def _key(client, year=2025):
    return _result(client, year)["people"][0]["key"]


def test_rent_under_the_ceiling_is_fully_exempt(client, db_session):
    _figures(db_session)
    prop = make_property(db_session, property_owner="Dad")
    _pay(db_session, _lease(db_session, prop, 5000), 5000, 2025, range(1, 13))

    # Nothing picked: only the list to pick from.
    empty = _report(client)
    assert empty["result"] is None
    [person] = empty["people"]
    assert person["name"] == "Dad" and [p["id"] for p in person["properties"]] == [prop.id]

    body = _report(client, ids=[prop.id])
    group = body["result"]
    [dad] = group["people"]
    assert dad["name"] == "Dad" and dad["other_income"] is None
    assert group["total_rent"] == 60000
    assert group["exemption"]["available"] is True
    assert group["exemption"]["taxable"] == 0
    assert group["exemption"]["tax"] == 0
    # The 10% is known at once; the surtax on it waits for the other income.
    assert group["ten_percent"]["base_tax"] == 6000
    assert group["ten_percent"]["tax"] is None
    assert group["ten_percent"]["missing"] == [{"code": "other_income", "key": dad["key"], "name": "Dad"}]
    assert group["checks"] == []
    assert body["figures"]["rent_exemption_ceiling_monthly"]["value"] == CEILING

    _other_income(client, group["people"][0]["key"], 200_000)
    ten = _result(client)["ten_percent"]
    assert ten["tax"] == 6000 and ten["surtax"] == 0
    assert "122" in body["figures"]["ten_percent_rate_pct"]["reference"]


def test_rent_between_the_ceiling_and_twice_it_is_partly_taxable(client, db_session):
    _figures(db_session)
    for address in ("1 Herzl", "2 Herzl"):
        prop = make_property(db_session, address=address, property_owner="Dad")
        _pay(db_session, _lease(db_session, prop, 4000), 4000, 2025, range(1, 13))

    exemption = _result(client)["exemption"]
    # 8,000 a month against 5,654: twice the excess is taxable.
    assert exemption["months"][0] == {"month": 1, "rent": 8000, "taxable": 4692}
    assert exemption["taxable"] == 4692 * 12
    # Taxed at the owner's marginal rate, which needs their other income.
    assert exemption["tax"] is None
    assert [m["code"] for m in exemption["missing"]] == ["other_income"]

    _other_income(client, _key(client), 200_000)
    exemption = _result(client)["exemption"]
    # Rent is not personal exertion, so under 60 it starts at 31% whatever the bracket.
    assert exemption["tax"] == pytest.approx(4692 * 12 * 0.31)


def test_above_twice_the_ceiling_all_rent_is_taxable(client, db_session):
    _figures(db_session)
    prop = make_property(db_session, property_owner="Dad")
    _pay(db_session, _lease(db_session, prop, 12000), 12000, 2025, [1])

    month = _result(client)["exemption"]["months"][0]
    assert month["taxable"] == 12000


def test_a_quarterly_payment_is_spread_over_its_months(client, db_session):
    _figures(db_session)
    prop = make_property(db_session, property_owner="Dad")
    renter = _lease(db_session, prop, 5000, number_of_payments=4)
    _pay(db_session, renter, 15000, 2025, [1, 4, 7, 10])

    group = _result(client)
    assert group["rent_by_month"] == [5000] * 12
    assert group["exemption"]["taxable"] == 0
    assert group["checks"] == []


def test_missing_and_above_lease_months_are_listed(client, db_session):
    _figures(db_session)
    prop = make_property(db_session, property_owner="Dad")
    renter = _lease(db_session, prop, 5000)
    _pay(db_session, renter, 5000, 2025, [m for m in range(1, 13) if m != 3])
    make_transaction(
        db_session, property_id=prop.id, renter_id=renter.id, amount=1000,
        month_for=date(2025, 6, 1), date_of_payment=date(2025, 6, 2),
    )

    checks = _result(client)["checks"]
    assert [(c["month"], c["kind"]) for c in checks] == [(3, "missing"), (6, "above_lease")]
    assert checks[1]["expected"] == 5000 and checks[1]["recorded"] == 6000


def test_a_company_renter_rules_out_the_exemption_but_not_the_10_percent_track(client, db_session):
    _figures(db_session)
    prop = make_property(db_session, property_owner="Dad")
    _pay(db_session, _lease(db_session, prop, 5000, is_company=True), 5000, 2025, range(1, 13))

    group = _result(client)
    assert group["exemption"]["available"] is False
    assert [p["id"] for p in group["exemption"]["company_renter"]] == [prop.id]
    assert group["ten_percent"]["base_tax"] == 6000


def test_commercial_properties_and_company_owners_are_left_out(client, db_session):
    _figures(db_session)
    home = make_property(db_session, address="Home", property_owner="Dad")
    shop = make_property(
        db_session, address="Shop", property_owner="Dad", type=PropertyTypeEnum.COMMERCIAL
    )
    make_property_owner(db_session, name="Levi Ltd", is_company=True)
    firm = make_property(db_session, address="Firm flat", property_owner="Levi Ltd")
    for prop in (home, shop, firm):
        _pay(db_session, _lease(db_session, prop, 5000), 5000, 2025, [1])

    people = _report(client)["people"]
    # The company is listed last, marked; the shop is listed, marked, and cannot be picked.
    assert [(p["name"], p["is_company"]) for p in people] == [("Dad", False), ("Levi Ltd", True)]
    assert [(p["id"], p["commercial"]) for p in people[0]["properties"]] == [
        (home.id, False), (shop.id, True),
    ]
    group = _report(client, ids=[home.id, shop.id, firm.id])["result"]
    assert [p["id"] for p in group["properties"]] == [home.id]
    assert group["total_rent"] == 5000


def test_owners_picked_together_share_one_ceiling(client, db_session):
    _figures(db_session)
    make_property_owner(db_session, name="Dad")
    make_property_owner(db_session, name="Mom")
    flats = []
    for owner in ("Dad", "Mom"):
        prop = make_property(db_session, address=owner, property_owner=owner)
        _pay(db_session, _lease(db_session, prop, 4000), 4000, 2025, [1])
        flats.append(prop)

    for prop in flats:
        alone = _report(client, ids=[prop.id])["result"]
        assert alone["exemption"]["taxable"] == 0
    household = _report(client, ids=[f.id for f in flats])["result"]
    assert [p["name"] for p in household["people"]] == ["Dad", "Mom"]
    assert household["exemption"]["months"][0]["taxable"] == 2 * (8000 - CEILING)


def test_a_couples_rent_is_added_to_the_higher_income(client, db_session):
    _figures(db_session)
    make_property_owner(db_session, name="Dad")
    make_property_owner(db_session, name="Mom")
    flats = []
    for owner in ("Dad", "Mom"):
        prop = make_property(db_session, address=owner, property_owner=owner)
        _pay(db_session, _lease(db_session, prop, 6000), 6000, 2025, range(1, 13))
        flats.append(prop)
    ids = [f.id for f in flats]
    dad, mom = _report(client, ids=ids)["result"]["people"]

    _other_income(client, dad["key"], 100_000)
    result = _report(client, ids=ids)["result"]
    # Mom's other income is still needed to know whose it is.
    assert result["attributed_key"] is None
    assert result["ten_percent"]["missing"] == [{"code": "other_income", "key": mom["key"], "name": "Mom"}]

    _other_income(client, mom["key"], 400_000)
    result = _report(client, ids=ids)["result"]
    assert result["attributed_key"] == mom["key"]
    assert [p["attributed"] for p in result["people"]] == [False, True]
    # Stacked on Mom's 400,000: past the 301,200 bracket, so 35% on the taxable rent.
    assert result["exemption"]["tax"] == pytest.approx(result["exemption"]["taxable"] * 0.35)


def test_the_current_year_fills_the_months_ahead_from_the_lease(client, db_session):
    _figures(db_session, year=2026)
    prop = make_property(db_session, property_owner="Dad")
    _pay(db_session, _lease(db_session, prop, 5000), 5000, 2026, range(1, 11))

    body = _report(client, 2026, [prop.id])
    group = body["result"]
    assert body["is_forecast"] is True and body["forecast_from_month"] == 11
    assert group["forecast_months"] == [11, 12]
    assert group["total_rent"] == 60000
    # October is not over, so it is not checked yet; November and December are forecast.
    assert group["checks"] == []


def test_a_year_without_figures_says_so_instead_of_guessing(client, db_session):
    prop = make_property(db_session, property_owner="Dad")
    _pay(db_session, _lease(db_session, prop, 5000), 5000, 2024, [1])

    group = _result(client, 2024)
    assert group["exemption"]["available"] is False
    assert group["exemption"]["missing"] == [{"code": "tax_figures"}]
    assert group["ten_percent"]["tax"] is None
    assert {"code": "tax_figures"} in group["marginal"]["missing"]


def test_years_outside_the_range_are_refused(client):
    assert client.get("/tax-tracks/report", params={"year": 2021}).status_code == 422
    assert client.get("/tax-tracks/report", params={"year": 2027}).status_code == 422


def test_not_available_outside_israel(client, db_session):
    db_session.add(Owner(id=OWNER_A, country="US"))
    db_session.commit()
    assert client.get("/tax-tracks/report", params={"year": 2025}).status_code == 404
    assert client.put("/tax-tracks/inputs", json={
        "person_key": "none", "year": 2025, "other_income": 1,
    }).status_code == 404


def test_flat_count_notes(client, db_session):
    _figures(db_session)
    for i in range(6):
        make_property(db_session, address=f"{i} Herzl", property_owner="Dad")
    notes = _result(client)["notes"]
    assert notes == [{"code": "maybe_business", "count": 6, "threshold": 6}]


# ── The marginal track ────────────────────────────────────────────────────────


def _cpi(db_session, readings):
    for (year, month), value in readings.items():
        db_session.add(CpiIndex(index_id=120010, year=year, month=month, value=value))
    db_session.commit()


def _expense(db_session, prop, key, amount, **kw):
    category = make_expense_category(db_session, owner_id=None, name=None, key=key)
    make_transaction(
        db_session, type=TransactionTypeEnum.EXPENSE, property_id=prop.id, amount=amount,
        date_of_payment=date(2025, 5, 1), categories=[category], **kw,
    )


def _bought_flat(db_session, **kw):
    make_property_owner(db_session, name="Dad", birth_date=date(1975, 3, 1))
    return make_property(
        db_session, property_owner="Dad", purchase_price=1_000_000,
        purchase_date=date(2019, 6, 1), **kw,
    )


def test_the_marginal_track_deducts_expenses_and_linked_depreciation(client, db_session):
    _figures(db_session)
    _cpi(db_session, {(2019, 12): 100.0, (2024, 12): 110.0, (2025, 12): 112.0})
    prop = _bought_flat(db_session)
    _pay(db_session, _lease(db_session, prop, 5000), 5000, 2025, range(1, 13))
    _expense(db_session, prop, "repairs", 3000)
    _expense(db_session, prop, "property_tax", 2000)
    _expense(db_session, prop, "maintenance", 10000, is_renovation=True)

    group = _result(client)
    assert group["marginal"]["available"] is False
    assert [m["code"] for m in group["marginal"]["missing"]] == ["other_income"]

    _other_income(client, group["people"][0]["key"], 200_000)
    group = _result(client)
    marginal = group["marginal"]
    [dep] = marginal["depreciation"]
    # Linked from December 2019 to December 2024, plus half of 2025's rise: 111 / 100.
    assert dep["factor"] == 1.11 and dep["months"] == 12
    assert dep["amount"] == 22200
    assert marginal["expenses"] == 3000
    assert marginal["income"] == 60000 - 3000 - 22200
    assert marginal["tax"] == pytest.approx(34800 * 0.31)
    # Only the recognised kinds count; the rest is shown, and a renovation is not an expense.
    expenses = group["expenses"]
    assert expenses["counted"] == [{"key": "repairs", "amount": 3000}]
    assert expenses["not_counted"] == [{"label": "property_tax", "built_in": True, "amount": 2000}]
    assert expenses["renovation"] == 10000
    # One flat: the mix is the single tracks.
    assert group["combination"] == {"available": False, "reason": "one_flat"}


def test_what_the_marginal_track_is_missing_is_listed_per_flat(client, db_session):
    _figures(db_session)
    prop = make_property(db_session, property_owner="Dad", purchase_date=None)
    _pay(db_session, _lease(db_session, prop, 5000), 5000, 2025, [1])
    mortgage = PropertyMortgage(property_id=prop.id, charge_day=10)
    mortgage.payments.append(MortgagePayment(amount=4000, effective_from=date(2020, 1, 1)))
    db_session.add(mortgage)
    db_session.commit()

    missing = _result(client)["marginal"]["missing"]
    assert [m["code"] for m in missing] == ["other_income", "interest", "purchase_date"]
    assert missing[1]["id"] == prop.id


def test_from_60_the_rent_is_on_the_personal_brackets(client, db_session):
    _figures(db_session)
    _cpi(db_session, {(2019, 12): 100.0, (2024, 12): 100.0, (2025, 12): 100.0})
    make_property_owner(db_session, name="Dad", birth_date=date(1965, 12, 31))
    prop = make_property(
        db_session, property_owner="Dad", purchase_price=1_000_000, purchase_date=date(2019, 6, 1)
    )
    _pay(db_session, _lease(db_session, prop, 5000), 5000, 2025, range(1, 13))
    _other_income(client, _key(client), 0, points=0)

    group = _result(client)
    # Turned 60 during 2025, which is what the law asks.
    assert group["senior"] is True
    # 60,000 rent less 20,000 depreciation, from 10%, and no credits (the owner said none).
    assert group["people"][0]["credit_points_asked"] is True
    assert group["people"][0]["credit_points_used"] == 0
    assert group["marginal"]["tax"] == pytest.approx(40000 * 0.10)


def test_credit_points_are_asked_only_when_other_income_is_low(client, db_session):
    _figures(db_session)
    prop = make_property(db_session, property_owner="Dad")
    _pay(db_session, _lease(db_session, prop, 5000), 5000, 2025, [1])
    key = _key(client)

    _other_income(client, key, 100_000, points=4)
    inputs = _result(client)["people"][0]
    assert inputs["credit_points_threshold"] == 65340
    assert inputs["credit_points_asked"] is False
    assert inputs["credit_points_used"] == 2.25

    _other_income(client, key, 30_000, points=4)
    inputs = _result(client)["people"][0]
    assert inputs["credit_points_asked"] is True and inputs["credit_points_used"] == 4


def test_a_couple_on_different_brackets_takes_the_brackets_of_whose_rent_it_is(client, db_session):
    _figures(db_session)
    make_property_owner(db_session, name="Dad", birth_date=date(1950, 1, 1))
    make_property_owner(db_session, name="Mom", birth_date=date(1980, 1, 1))
    for owner in ("Dad", "Mom"):
        prop = make_property(db_session, address=owner, property_owner=owner)
        _pay(db_session, _lease(db_session, prop, 4000), 4000, 2025, [1])
    group = _result(client)
    assert group["senior"] is None
    dad, mom = group["people"]
    assert dad["senior"] is True and mom["senior"] is False

    # The same income and different ages: which is the registered spouse decides it.
    _other_income(client, dad["key"], 50_000)
    _other_income(client, mom["key"], 50_000)
    group = _result(client)
    assert group["attributed_key"] is None
    assert {"code": "mixed_ages"} in group["marginal"]["missing"]

    _other_income(client, dad["key"], 80_000)
    group = _result(client)
    assert group["attributed_key"] == dad["key"] and group["senior"] is True
    assert {"code": "mixed_ages"} not in group["marginal"]["missing"]


def test_the_cheapest_mix_puts_each_flat_on_its_own_track(client, db_session):
    _figures(db_session)
    _cpi(db_session, {(2019, 12): 100.0, (2024, 12): 100.0, (2025, 12): 100.0})
    make_property_owner(db_session, name="Dad", birth_date=date(1975, 3, 1))
    flats = []
    for address, rent in (("Small", 3000), ("Big", 9000)):
        prop = make_property(
            db_session, address=address, property_owner="Dad", purchase_price=500_000,
            purchase_date=date(2019, 6, 1),
        )
        _pay(db_session, _lease(db_session, prop, rent), rent, 2025, range(1, 13))
        flats.append(prop)
    _other_income(client, _key(client), 200_000)

    group = _result(client)
    mix = group["combination"]
    singles = [group["exemption"]["tax"], group["ten_percent"]["tax"], group["marginal"]["tax"]]
    assert mix["available"] is True
    assert mix["tax"] <= min(singles)
    assert {f["id"] for f in mix["flats"]} == {p.id for p in flats}


def test_ten_percent_rent_counts_toward_the_surtax(client, db_session):
    _figures(db_session)
    prop = make_property(db_session, property_owner="Dad")
    _pay(db_session, _lease(db_session, prop, 800_000), 800_000, 2025, [1])
    _other_income(client, _key(client), 0)

    ten = _result(client)["ten_percent"]
    assert ten["surtax"] == pytest.approx(0.03 * (800_000 - 721_560))
    assert ten["capital_surtax"] == pytest.approx(0.02 * (800_000 - 721_560))
    assert ten["tax"] == pytest.approx(80_000 + ten["surtax"] + ten["capital_surtax"])


def test_inputs_are_kept_per_person(client, db_session):
    _figures(db_session)
    dad = make_property_owner(db_session, name="Dad")
    assert client.put("/tax-tracks/inputs", json={
        "person_key": "owner:999", "year": 2025, "other_income": 1,
    }).status_code == 404
    assert client.put("/tax-tracks/inputs", json={
        "person_key": "custom:1", "year": 2025, "other_income": 1,
    }).status_code == 422
    assert client.put("/tax-tracks/inputs", json={
        "person_key": "none", "year": 2027, "other_income": 1,
    }).status_code == 422

    _other_income(client, f"owner:{dad.id}", 50_000)
    _other_income(client, "none", 10_000)
    rows = {r.person_key: r.other_income for r in db_session.query(TaxYearInput)}
    assert rows == {f"owner:{dad.id}": 50_000, "none": 10_000}


def _mortgaged(db_session, prop, years=()):
    mortgage = PropertyMortgage(property_id=prop.id, charge_day=10)
    mortgage.payments.append(MortgagePayment(amount=4000, effective_from=date(2020, 1, 1)))
    for year, interest, linkage in years:
        mortgage.years.append(MortgageYear(year=year, interest=interest, linkage=linkage))
    db_session.add(mortgage)
    db_session.commit()
    return mortgage


def test_mortgage_interest_and_linkage_are_deducted(client, db_session):
    _figures(db_session)
    _cpi(db_session, {(2019, 12): 100.0, (2024, 12): 100.0, (2025, 12): 100.0})
    prop = _bought_flat(db_session)
    _pay(db_session, _lease(db_session, prop, 5000), 5000, 2025, range(1, 13))
    mortgage = _mortgaged(db_session, prop)
    _other_income(client, _key(client), 200_000)

    [missing] = _result(client)["marginal"]["missing"]
    assert missing["code"] == "interest" and missing["mortgage_id"] == mortgage.id

    mortgage.years.append(MortgageYear(year=2025, interest=9000, linkage=1000))
    db_session.commit()
    marginal = _result(client)["marginal"]
    assert marginal["interest_total"] == 10000
    assert marginal["interest"][0]["estimated"] is False
    # 60,000 rent − 20,000 depreciation − 10,000 interest and linkage.
    assert marginal["income"] == 30000


def test_the_current_year_estimates_with_last_years_interest(client, db_session):
    _figures(db_session, year=2026)
    prop = make_property(db_session, property_owner="Dad", purchase_date=date(2019, 6, 1))
    _pay(db_session, _lease(db_session, prop, 5000), 5000, 2026, range(1, 11))
    _mortgaged(db_session, prop, years=[(2025, 9000, 0)])

    marginal = _result(client, 2026)["marginal"]
    [interest] = marginal["interest"]
    assert interest["estimated"] is True and interest["year"] == 2025
    assert not any(m["code"] == "interest" for m in marginal["missing"])

"""The tax tracks report (PLATFORM.md §12; plan in ``docs/תוכנית השוואת מסלולי מס בדוחות.md``).

For one tax year and the flats the account holder picked (one household: a person, or a
couple), it works out the three tracks — the exemption, 10% and the marginal rate — and the
cheapest choice of one track per flat. Every government figure comes from ``tax_values`` for that year, with its
source; none is written in this file or in ``tax_tracks_math``.

What the numbers are built from:

- **Rent by the month it is for** (``month_for``, else the day paid). A payment that covers
  several months — a quarterly or yearly lease records one row per period — is spread evenly
  over them, because the exemption ceiling is tested month by month.
- **Residential properties only.** Commercial ones are left out, and so is everything owned by
  a company, which the individual tracks do not apply to. Both are named in the result.
- **The current year** is what was recorded so far plus, for the months still ahead, what the
  leases say. Those months are flagged as a forecast.

- **The marginal track** takes the expenses in ``RECOGNISED_EXPENSE_KEYS`` (renovations
  left out), depreciation under the 1989 regulations, mortgage interest from the bank's yearly
  statement (``MortgageYear``: interest and linkage differences together), and the
  other income and credit points of the person the rent is added to, from
  ``tax_year_inputs`` (one row per person and year). A flat whose mortgage
  ran that year without the year's statement lists it as missing; the current year, whose
  statement cannot exist yet, uses last year's as an estimate.

Nothing is assumed. A track that needs a figure we do not have says what is missing instead
of being computed, and the cheapest mix is offered only once every track can be.
"""
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from itertools import product
from typing import Iterable

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException

from app.models.property import Property, PropertyTypeEnum
from app.models.renter import Renter
from app.repositories.cpi_index_repository import CpiIndexRepository
from app.repositories.property_owner_repository import PropertyOwnerRepository
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.repositories.tax_value_repository import TaxValueRepository
from app.repositories.tax_year_input_repository import TaxYearInputRepository
from app.repositories.transaction_repository import TransactionRepository
from app.services import country_service
from app.services.cpi_rent_change import parse_lease_years
from app.services.lease_periods import rent_for_month
from app.services.renter_service import _payment_interval_months
from app.services.tax_tracks_math import (
    exempt_amount,
    income_for_tax,
    income_tax_on_rent,
    surtax_on_rent,
)

# The figures the tracks read, and the notes beside them.
_USED_KEYS = (
    "rent_exemption_ceiling_monthly",
    "ten_percent_rate_pct",
    "personal_brackets",
    "other_income_brackets",
    "senior_age",
    "credit_point_monthly",
    "resident_credit_points",
    "surtax_threshold",
    "surtax_rate_pct",
    "capital_surtax_rate_pct",
    "depreciation_rate_pct",
    "rent_paid_deduction_cap",
    "passive_max_apartments",
    "business_min_apartments",
)

# Expenses the marginal track deducts. No official list was found; these are the kinds the
# sources name (ongoing repairs, insurance, a lawyer for the lease). Everything else is shown
# beside the result as not counted, for the accountant. Mortgage interest is not here: it
# comes from the mortgage, so it cannot be counted twice.
RECOGNISED_EXPENSE_KEYS = frozenset({"repairs", "maintenance", "insurance", "legal_professional"})

TRACKS = ("exemption", "ten_percent", "marginal")

# Trying every mix is 3^n; ten flats is where renting is likely a business anyway.
_MAX_MIX_FLATS = 9

# A recorded amount within this of the lease's is the same amount (agorot, rounding).
_TOLERANCE = 1.0

NO_OWNER = "none"


def _month_index(d: date) -> int:
    return d.year * 12 + d.month - 1


def _covers(renter: Renter, year: int, month: int) -> bool:
    """Whether the lease runs in this calendar month.

    ``lease_end`` is the day *after* the schedule (start plus its months), so the month it
    falls in belongs to the next lease, not this one. ``terminated_on`` is the last day the
    tenant was there, so a month that started before it still counts.
    """
    if renter.lease_start is None:
        return False
    index = year * 12 + month - 1
    if index < _month_index(renter.lease_start):
        return False
    if renter.lease_end is not None and index >= _month_index(renter.lease_end):
        return False
    if renter.terminated_on is not None and date(year, month, 1) >= renter.terminated_on:
        return False
    return True


def _lease_rent(renter: Renter, year: int, month: int) -> float:
    return float(rent_for_month(parse_lease_years(renter.lease_years), renter.lease_start, date(year, month, 1)))


@dataclass
class _Ledger:
    """Rent per property and month for the year, and who it came from."""

    rent: dict[int, list[float]] = field(default_factory=lambda: defaultdict(lambda: [0.0] * 12))
    company: dict[int, list[float]] = field(default_factory=lambda: defaultdict(lambda: [0.0] * 12))
    forecast: dict[int, list[bool]] = field(default_factory=lambda: defaultdict(lambda: [False] * 12))
    by_renter: dict[tuple[int, int], float] = field(default_factory=lambda: defaultdict(float))


class TaxTracksService:
    def __init__(
        self,
        properties: PropertyRepository,
        property_owners: PropertyOwnerRepository,
        renters: RenterRepository,
        transactions: TransactionRepository,
        tax_values: TaxValueRepository,
        inputs: TaxYearInputRepository,
        cpi: CpiIndexRepository,
    ):
        self.properties = properties
        self.property_owners = property_owners
        self.renters = renters
        self.transactions = transactions
        self.tax_values = tax_values
        self.inputs = inputs
        self.cpi = cpi

    # ── Scope ────────────────────────────────────────────────────────────────

    @staticmethod
    def country_for(account_country: str | None) -> tuple[str, int]:
        """The country code and first tax year the report runs for, or 404 where it does not."""
        code = country_service.config_for(account_country).country_code
        tax_data = country_service.tax_data_by_country().get(code)
        if tax_data is None:
            raise HTTPException(status_code=404, detail="The tax report is not available here")
        return code, tax_data.first_year

    # ── Other income and credit points ───────────────────────────────────────

    def save_inputs(
        self,
        owner_id: str,
        person_key: str,
        year: int,
        first_year: int,
        today: date,
        other_income: float | None,
        credit_points: float | None,
    ) -> dict:
        if not first_year <= year <= today.year:
            raise HTTPException(status_code=422, detail="No tax report for that year")
        kind, _, raw_id = person_key.partition(":")
        if person_key != NO_OWNER and not (
            kind == "owner"
            and raw_id.isdigit()
            and any(
                o.id == int(raw_id)
                for o in self.property_owners.get_all(owner_id, include_inactive=True)
            )
        ):
            raise HTTPException(status_code=404, detail="Property owner not found")
        row = self.inputs.save(owner_id, person_key, year, other_income, credit_points)
        return {
            "person_key": row.person_key,
            "year": row.year,
            "other_income": row.other_income,
            "credit_points": row.credit_points,
        }

    # ── Report ───────────────────────────────────────────────────────────────

    def report(
        self,
        owner_id: str,
        account_country: str | None,
        year: int,
        today: date,
        hidden_property_ids: Iterable[int] = (),
        selected_property_ids: Iterable[int] = (),
    ) -> dict:
        """The report for the flats picked, and the owners and flats there are to pick from.

        The flats picked are worked out as one household. Ids that are not the account's
        residential flats in the country (deleted since, commercial, a company's) are
        skipped, so a selection the browser remembered cannot fail the page.
        """
        country, first_year = self.country_for(account_country)
        if not first_year <= year <= today.year:
            raise HTTPException(status_code=422, detail="No tax report for that year")
        hidden = set(hidden_property_ids)

        entries = self.tax_values.entries_for(country, year)
        values = {key: _decoded(row.value) for key, row in entries.items()}
        document = self.tax_values.document(country, year)

        owners = {o.id: o for o in self.property_owners.get_all(owner_id, include_inactive=True)}
        in_country = [
            p
            for p in self.properties.get_all_by_owner(owner_id)
            if country_service.config_for(p.country).country_code == country
        ]
        visible = [p for p in in_country if p.id not in hidden]

        company_owner_ids = {oid for oid, o in owners.items() if o.is_company}
        residential = [
            p
            for p in visible
            if p.type != PropertyTypeEnum.COMMERCIAL and p.property_owner_id not in company_owner_ids
        ]
        wanted = set(selected_property_ids)
        chosen = [p for p in residential if p.id in wanted]
        chosen_ids = {p.id for p in chosen}
        renters = [r for r in self.renters.get_all(owner_id) if r.property_id in chosen_ids]
        ledger = self._ledger(owner_id, year, today, chosen, renters)

        renters_by_property: dict[int, list[Renter]] = defaultdict(list)
        for r in renters:
            renters_by_property[r.property_id].append(r)
        expenses: dict[int, list] = defaultdict(list)
        for tx in self.transactions.expenses_between(
            owner_id, [p.id for p in chosen], date(year, 1, 1), date(year, 12, 31)
        ):
            expenses[tx.property_id].append(tx)
        series = country_service.index_series_for(country)
        series_id = series.series_id if series is not None else None
        ctx = _Context(
            year=year,
            values=values,
            ledger=ledger,
            checks=self._checks(year, today, renters, ledger),
            inputs=self.inputs.for_year(owner_id, year),
            expenses=expenses,
            renters_by_property=renters_by_property,
            owners=owners,
            cpi=_Cpi(
                self.cpi,
                series_id,
                self.cpi.earliest_period(series_id) if series_id is not None else None,
            ),
            is_forecast=year == today.year,
        )

        return {
            "year": year,
            "country": country,
            "years": list(range(first_year, today.year + 1)),
            "is_forecast": year == today.year,
            "forecast_from_month": today.month + 1 if year == today.year else None,
            "figures": {
                key: {
                    "value": values[key],
                    "source": entries[key].source,
                    "reference": entries[key].reference,
                    "url": entries[key].url,
                }
                for key in _USED_KEYS
                if key in entries
            },
            "document": (
                {"url": document.url, "fetched_at": document.fetched_at.date().isoformat()}
                if document is not None
                else None
            ),
            "people": _catalog(visible, owners),
            "locked_properties": len([p for p in in_country if p.id in hidden]),
            "result": self._household(chosen, ctx) if chosen else None,
        }

    def _ledger(
        self, owner_id: str, year: int, today: date, properties: list[Property], renters: list[Renter]
    ) -> _Ledger:
        ledger = _Ledger()
        by_id = {r.id: r for r in renters}
        # A yearly payment recorded for last February still covers this January.
        rows = self.transactions.revenue_between(
            owner_id, [p.id for p in properties], date(year - 1, 2, 1), date(year, 12, 31)
        )
        for tx in rows:
            renter = by_id.get(tx.renter_id)
            interval = _payment_interval_months(renter.number_of_payments) if renter else 1
            start = (tx.month_for or tx.date_of_payment).replace(day=1)
            share = float(tx.amount) / interval
            for k in range(interval):
                month = start + relativedelta(months=k)
                if month.year != year:
                    continue
                ledger.rent[tx.property_id][month.month - 1] += share
                if renter is not None:
                    ledger.by_renter[(renter.id, month.month)] += share
                    if renter.is_company:
                        ledger.company[tx.property_id][month.month - 1] += share

        if year == today.year:
            # The months ahead: what the lease says, unless rent for them is already in.
            for renter in renters:
                for month in range(today.month + 1, 13):
                    if not _covers(renter, year, month) or ledger.by_renter.get((renter.id, month)):
                        continue
                    amount = _lease_rent(renter, year, month)
                    if amount <= 0:
                        continue
                    ledger.rent[renter.property_id][month - 1] += amount
                    ledger.forecast[renter.property_id][month - 1] = True
                    if renter.is_company:
                        ledger.company[renter.property_id][month - 1] += amount
        return ledger

    @staticmethod
    def _checks(year: int, today: date, renters: list[Renter], ledger: _Ledger) -> dict[int, list[dict]]:
        """Months where the rent recorded is missing or above the lease, per property.

        Only months that are over: the current one may not have been paid yet.
        """
        last = 12 if year < today.year else today.month - 1
        out: dict[int, list[dict]] = defaultdict(list)
        for renter in renters:
            for month in range(1, last + 1):
                if not _covers(renter, year, month):
                    continue
                expected = _lease_rent(renter, year, month)
                recorded = ledger.by_renter.get((renter.id, month), 0.0)
                if expected > 0 and recorded < _TOLERANCE:
                    kind = "missing"
                elif recorded > expected + _TOLERANCE:
                    kind = "above_lease"
                else:
                    continue
                out[renter.property_id].append({
                    "renter_id": renter.id,
                    "renter_name": f"{renter.first_name} {renter.last_name}".strip(),
                    "property_id": renter.property_id,
                    "month": month,
                    "kind": kind,
                    "expected": round(expected, 2),
                    "recorded": round(recorded, 2),
                })
        return out

    # ── The household ────────────────────────────────────────────────────────────

    def _household(self, props: list[Property], ctx: "_Context") -> dict:
        ledger, values = ctx.ledger, ctx.values
        result: dict = {}
        monthly = [round(sum(ledger.rent[p.id][m] for p in props), 2) for m in range(12)]
        forecast = [any(ledger.forecast[p.id][m] for p in props) for m in range(12)]
        gross = round(sum(monthly), 2)
        result["properties"] = [
            {**_property_brief(p), "rent": round(sum(ledger.rent[p.id]), 2)} for p in props
        ]
        result["rent_by_month"] = monthly
        result["forecast_months"] = [m + 1 for m in range(12) if forecast[m]]
        result["total_rent"] = gross
        result["checks"] = [c for p in props for c in ctx.checks.get(p.id, [])]

        # Only flats that earned rent this year are on a track; an empty one has nothing to tax.
        flats = [self._flat(p, ctx) for p in props if sum(ledger.rent[p.id]) > 0]
        household = _Household.of(props, ctx)
        result["people"] = household.people
        result["attributed_key"] = household.attributed["key"] if household.attributed else None
        result["senior"] = household.senior
        payer = _Payer(values, household.other_income, household.credits, household.senior, household.waiting)

        result["exemption"] = self._exemption(flats, monthly, gross, payer, household)
        result["ten_percent"] = _ten_percent(flats, gross, payer)
        result["marginal"] = _marginal(flats, payer, household, ctx)
        result["combination"] = _combination(flats, payer, result)
        result["expenses"] = _expense_summary(flats)

        notes = list(household.notes)
        count = len(props)
        passive_max = values.get("passive_max_apartments")
        business_min = values.get("business_min_apartments")
        if business_min is not None and count >= business_min:
            notes.append({"code": "likely_business", "count": count, "threshold": business_min})
        elif passive_max is not None and count > passive_max:
            notes.append({"code": "maybe_business", "count": count, "threshold": passive_max + 1})
        cap = values.get("rent_paid_deduction_cap")
        if count == 1 and cap is not None:
            notes.append({"code": "rent_paid_deduction", "cap": cap})
        result["notes"] = notes
        return result

    @staticmethod
    def _exemption(flats: list["_Flat"], monthly: list[float], gross: float, payer: "_Payer", person: "_Household") -> dict:
        ceiling = payer.values.get("rent_exemption_ceiling_monthly")
        company = [f for f in flats if f.company]
        exemption: dict = {
            "available": False,
            "ceiling": ceiling,
            "missing": [],
            "company_renter": [_property_brief(f.property) for f in company],
        }
        if ceiling is None:
            exemption["missing"].append({"code": "tax_figures"})
            return exemption
        if company:
            # A company renter rules the exemption out for its flat. The whole-household figure
            # would be wrong; the per-flat mix below can still use it on the other flats.
            return exemption
        months = []
        for m in range(12):
            rent = monthly[m]
            taxable = rent - exempt_amount(rent, rent, ceiling)
            months.append({"month": m + 1, "rent": rent, "taxable": round(taxable, 2)})
        taxable_total = round(sum(x["taxable"] for x in months), 2)
        exemption.update({
            "available": True,
            "months": months,
            "taxable": taxable_total,
            "exempt": round(gross - taxable_total, 2),
            "tax": None,
        })
        if taxable_total == 0:
            exemption["tax"] = 0.0
            return exemption
        # The taxable part is taxed at the marginal rate, after its share of the expenses.
        missing = payer.missing() + person.missing + [m for f in flats for m in f.interest_missing]
        exemption["missing"] = missing
        if not missing:
            exemption.update(_assessment({f.property.id: "exemption" for f in flats}, flats, payer))
        return exemption

    # ── Per flat ─────────────────────────────────────────────────────────────

    def _flat(self, p: Property, ctx: "_Context") -> "_Flat":
        flat = _Flat(property=p, rent_by_month=list(ctx.ledger.rent[p.id]))
        flat.company = sum(ctx.ledger.company[p.id]) > 0
        for tx in ctx.expenses.get(p.id, []):
            amount = float(tx.amount)
            category = tx.categories[0] if tx.categories else tx.category
            key = category.key if category is not None else None
            label = (category.key or category.name) if category is not None else None
            if tx.is_renovation:
                flat.renovation += amount
            elif key in RECOGNISED_EXPENSE_KEYS:
                flat.counted[key] += amount
            else:
                flat.not_counted[(label, key is not None)] += amount
        mortgage = p.mortgage
        if mortgage is not None and _mortgage_runs_in(mortgage, ctx.year):
            # The monthly payment is principal and interest together; what was interest is
            # in the bank's yearly statement. Linkage differences count with it (section 17,
            # "interest and linkage differences").
            entry = mortgage.year(ctx.year)
            estimated = False
            if entry is None and ctx.is_forecast:
                # This year's statement comes next year; last year's stands in, marked.
                entry = mortgage.year(ctx.year - 1)
                estimated = entry is not None
            if entry is None:
                flat.interest_missing.append(
                    {"code": "interest", "mortgage_id": mortgage.id, **_property_brief(p)}
                )
            else:
                flat.interest = float(entry.interest) + float(entry.linkage or 0)
                flat.interest_detail = {
                    **_property_brief(p),
                    "year": entry.year,
                    "interest": float(entry.interest),
                    "linkage": float(entry.linkage or 0),
                    "amount": round(flat.interest, 2),
                    "estimated": estimated,
                }
        flat.depreciation = self._depreciation(p, ctx)
        return flat

    def _depreciation(self, p: Property, ctx: "_Context") -> dict:
        """Depreciation under the 1989 regulations: the purchase price, linked to the CPI,
        × the rate × the full months rented ÷ 12.

        Linked from the end of the purchase year to the start of the tax year, plus half the
        index's rise during the tax year. For the current year the year's last reading is not
        out, so the latest one stands in, and the result says which.
        """
        rate = ctx.values.get("depreciation_rate_pct")
        months = sum(
            1 for m in range(1, 13)
            if any(_full_month(r, ctx.year, m) for r in ctx.renters_by_property.get(p.id, []))
        )
        out: dict = {"months": months, "rate_pct": rate, "missing": [], "amount": None}
        if rate is None:
            out["missing"].append({"code": "tax_figures"})
        if p.purchase_price is None or p.purchase_price <= 0:
            out["missing"].append({"code": "purchase_price", **_property_brief(p)})
        if p.purchase_date is None:
            out["missing"].append({"code": "purchase_date", **_property_brief(p)})
        if out["missing"]:
            return out
        bought = p.purchase_date.year
        out.update({"price": float(p.purchase_price), "purchase_year": bought})
        if bought >= ctx.year:
            factor, linked = 1.0, None
        else:
            cpi = ctx.cpi
            if cpi.floor is not None and (bought, 12) < cpi.floor:
                # Readings before the series' start are on the old high-inflation bases and
                # do not chain; with no index, there is no linked value to depreciate.
                out.update({"amount": 0.0, "before_cpi": True})
                return out
            base = cpi.value(bought, 12)
            start = cpi.value(ctx.year - 1, 12)
            end, end_period = cpi.year_end(ctx.year)
            if base is None or start is None or end is None:
                out["missing"].append({"code": "cpi", **_property_brief(p)})
                return out
            factor = (start + (end - start) / 2) / base
            linked = {"from": [bought, 12], "start": [ctx.year - 1, 12], "end": list(end_period),
                      "estimated": end_period != (ctx.year, 12)}
        value = float(p.purchase_price) * factor
        out.update({
            "factor": round(factor, 4),
            "cpi": linked,
            "value": round(value, 2),
            "amount": round(value * rate / 100 * months / 12, 2),
        })
        return out


@dataclass
class _Flat:
    property: Property
    rent_by_month: list[float]
    company: bool = False
    counted: dict = field(default_factory=lambda: defaultdict(float))
    not_counted: dict = field(default_factory=lambda: defaultdict(float))
    renovation: float = 0.0
    interest: float = 0.0
    interest_detail: dict | None = None
    interest_missing: list = field(default_factory=list)
    depreciation: dict = field(default_factory=dict)

    @property
    def rent(self) -> float:
        return sum(self.rent_by_month)

    @property
    def expenses(self) -> float:
        return sum(self.counted.values())

    @property
    def deductions(self) -> float:
        """Everything deductible from the rent but depreciation: expenses and interest."""
        return self.expenses + self.interest


@dataclass
class _Cpi:
    """The country's index readings, by month."""

    repository: CpiIndexRepository | None
    series_id: int | None
    floor: tuple[int, int] | None

    def value(self, year: int, month: int) -> float | None:
        if self.repository is None or self.series_id is None:
            return None
        return self.repository.value_for(self.series_id, year, month)

    def year_end(self, year: int) -> tuple[float | None, tuple[int, int]]:
        """December's reading, or for a year still running the latest one in it (or last
        December's, in January)."""
        for month in range(12, 0, -1):
            found = self.value(year, month)
            if found is not None:
                return found, (year, month)
        return self.value(year - 1, 12), (year - 1, 12)


@dataclass
class _Context:
    year: int
    values: dict
    ledger: "_Ledger"
    checks: dict
    inputs: dict
    expenses: dict
    renters_by_property: dict
    owners: dict
    cpi: _Cpi
    is_forecast: bool


def _senior(owner, ctx: _Context, notes: list) -> bool:
    """Whether the owner is taxed on the 60-and-over brackets that year. No birth date and
    no tick is taken as under 60, and the page says so."""
    age = ctx.values.get("senior_age")
    if owner.birth_date is not None:
        return age is not None and ctx.year - owner.birth_date.year >= age
    if owner.over_60:
        marked = owner.over_60_marked_on
        if marked is not None and marked.year > ctx.year:
            # Ticked by hand in a later year: we do not know about this one.
            notes.append({"code": "over_60_ticked_later", "name": owner.name, "year": marked.year})
            return False
        return True
    return False


@dataclass
class _Household:
    """The people whose flats were picked, and whose income their rent is added to.

    A married couple's rent is added to the spouse whose income from work is higher, and when
    neither works, to the registered spouse (Income Tax Ordinance section 66; PLATFORM.md
    §12). The report takes the person with the higher other income the account holder
    entered. On a tie the two are the same for the brackets unless only one is 60 or over;
    the report then asks (``mixed_ages``) instead of choosing.

    The rent is never split between them. Section 66(b) lets a spouse who owned a flat a year
    before the marriage, or inherited it, have its rent added to their own income instead;
    the report does not offer that, and says so.
    """

    people: list[dict]
    attributed: dict | None
    senior: bool | None
    other_income: float | None
    credits: float | None
    # The other income still missing, per person: it blocks the tax on any taxable rent.
    waiting: list
    # The age question on a tie, which only the brackets need.
    missing: list
    notes: list

    @staticmethod
    def of(props: list[Property], ctx: _Context) -> "_Household":
        notes: list = []
        people: list[dict] = []
        owner_ids = {p.property_owner_id for p in props if p.property_owner_id in ctx.owners}
        for oid in sorted(owner_ids, key=lambda i: (ctx.owners[i].name or "").lower()):
            owner = ctx.owners[oid]
            people.append({
                "key": f"owner:{oid}",
                "owner_id": oid,
                "name": owner.name,
                "senior": _senior(owner, ctx, notes),
                "age_from": "birth_date" if owner.birth_date else "ticked" if owner.over_60 else None,
            })
        if any(p.property_owner_id not in ctx.owners for p in props):
            notes.append({"code": "no_owner_age"})
            people.append({"key": NO_OWNER, "owner_id": None, "name": None, "senior": False,
                           "age_from": None})
        for person in people:
            row = ctx.inputs.get(person["key"])
            person["other_income"] = row.other_income if row is not None else None
            person["credit_points"] = row.credit_points if row is not None else None
            person["attributed"] = False

        waiting = [
            {"code": "other_income", "key": p["key"], "name": p["name"]}
            for p in people
            if p["other_income"] is None
        ]
        missing: list = []
        attributed = None
        payer = people[0]
        if not waiting:
            top = max(p["other_income"] for p in people)
            tied = [p for p in people if p["other_income"] == top]
            payer = tied[0]
            if len({p["senior"] for p in tied}) > 1:
                missing.append({"code": "mixed_ages"})
            else:
                attributed = payer
                payer["attributed"] = True
        statuses = {p["senior"] for p in people}
        if attributed is not None:
            senior = attributed["senior"]
        elif len(statuses) == 1:
            senior = statuses.pop()
        else:
            senior = None
        other = payer["other_income"] if not waiting else None
        credits, inputs = _credits(ctx.values, other, payer["credit_points"])
        for person in people:
            asked = person is payer and inputs["credit_points_asked"]
            person["credit_points_asked"] = asked
            person["credit_points_used"] = inputs["credit_points_used"] if person is payer else None
            person["credit_points_threshold"] = inputs["credit_points_threshold"]
        return _Household(people, attributed, senior, other, credits, waiting, missing, notes)


@dataclass
class _Payer:
    """The household's tax position for the year: what the rent is stacked on."""

    values: dict
    other_income: float | None
    credits: float | None
    senior: bool | None = None
    # The household's other income still missing, per person.
    waiting: list = field(default_factory=list)

    _NEEDED = ("personal_brackets", "other_income_brackets", "surtax_threshold",
               "surtax_rate_pct", "credit_point_monthly", "resident_credit_points", "senior_age")

    def missing(self) -> list[dict]:
        out = []
        if any(self.values.get(k) is None for k in self._NEEDED):
            out.append({"code": "tax_figures"})
        return out + self.waiting

    def income_tax(self, rent_income: float) -> float:
        rent_table = self.values["personal_brackets" if self.senior else "other_income_brackets"]
        return income_tax_on_rent(
            self.values["personal_brackets"], rent_table, self.other_income, rent_income, self.credits
        )

    def surtax(self, rent_income: float) -> tuple[float, float]:
        return surtax_on_rent(
            self.values["surtax_threshold"],
            self.values["surtax_rate_pct"],
            self.values.get("capital_surtax_rate_pct"),
            self.other_income,
            rent_income,
        )


def _credits(values: dict, other: float | None, stored: float | None) -> tuple[float | None, dict]:
    """The credit points the rent's tax may use, and what the form should ask.

    Above the income at which a resident's basic points are used up, they are taken as used
    by the other income and not asked. Below it, the account holder says how many they have.
    """
    point = values.get("credit_point_monthly")
    basic = values.get("resident_credit_points")
    personal = values.get("personal_brackets")
    inputs = {
        "credit_points_asked": False,
        "credit_points_used": None,
        "credit_points_threshold": None,
    }
    if point is None or basic is None or personal is None:
        return None, inputs
    threshold = income_for_tax(personal, basic * point * 12)
    asked = other is not None and other < threshold
    points = stored if asked and stored is not None else basic
    inputs.update({
        "credit_points_asked": asked,
        "credit_points_used": points,
        "credit_points_threshold": round(threshold),
    })
    return points * point * 12, inputs


def _assessment(tracks: dict[int, str], flats: list[_Flat], payer: _Payer) -> dict:
    """The year's tax on the household's rent with each flat on the track ``tracks`` gives it.

    Exemption: the reduced ceiling is tested month by month against the rent of every flat,
    whatever its track; the taxable part keeps its share of the track's expenses. 10%: of
    the gross. Marginal: rent less expenses and depreciation. What is taxable on the first
    and last is stacked on the other income; all of it, the 10% rent included, counts
    toward the surtax.
    """
    ceiling = payer.values.get("rent_exemption_ceiling_monthly")
    rate = payer.values.get("ten_percent_rate_pct")
    by_track = {t: [f for f in flats if tracks[f.property.id] == t] for t in TRACKS}

    exemption_rent = sum(f.rent for f in by_track["exemption"])
    exemption_taxable = 0.0
    if by_track["exemption"]:
        for m in range(12):
            track = sum(f.rent_by_month[m] for f in by_track["exemption"])
            every = sum(f.rent_by_month[m] for f in flats)
            exemption_taxable += track - exempt_amount(track, every, ceiling)
    share = exemption_taxable / exemption_rent if exemption_rent else 0.0
    exemption_deductions = sum(f.deductions for f in by_track["exemption"]) * share
    exemption_income = max(0.0, exemption_taxable - exemption_deductions)

    ten_gross = sum(f.rent for f in by_track["ten_percent"])
    ten_tax = ten_gross * rate / 100 if by_track["ten_percent"] else 0.0

    marginal_net = sum(
        f.rent - f.deductions - (f.depreciation.get("amount") or 0.0) for f in by_track["marginal"]
    )
    marginal_income = max(0.0, marginal_net)

    ordinary = exemption_income + marginal_income
    income_tax = payer.income_tax(ordinary)
    general, capital = payer.surtax(ordinary + ten_gross)
    return {
        "taxable_income": round(ordinary, 2),
        "exemption_deductions": round(exemption_deductions, 2),
        "marginal_loss": marginal_net < 0,
        "ten_percent_tax": round(ten_tax, 2),
        "income_tax": round(income_tax, 2),
        "surtax": round(general, 2),
        "capital_surtax": round(capital, 2),
        "tax": round(ten_tax + income_tax + general + capital, 2),
    }


def _ten_percent(flats: list[_Flat], gross: float, payer: _Payer) -> dict:
    rate = payer.values.get("ten_percent_rate_pct")
    out: dict = {
        "available": rate is not None,
        "rate_pct": rate,
        "gross": gross,
        "base_tax": round(gross * rate / 100, 2) if rate is not None else None,
        "tax": None,
        "missing": [] if rate is not None else [{"code": "tax_figures"}],
    }
    if rate is None:
        return out
    if gross == 0:
        out["tax"] = 0.0
        return out
    # The surtax counts this rent too (decided Oct 19, 2026; see the plan's "not found").
    out["missing"] = payer.missing()
    if not out["missing"]:
        result = _assessment({f.property.id: "ten_percent" for f in flats}, flats, payer)
        out.update({k: result[k] for k in ("surtax", "capital_surtax", "tax")})
    return out


def _marginal(flats: list[_Flat], payer: _Payer, person: _Household, ctx: _Context) -> dict:
    missing = payer.missing() + person.missing
    for f in flats:
        missing += f.interest_missing + f.depreciation["missing"]
    out: dict = {
        "available": False,
        "missing": _unique(missing),
        "rent": round(sum(f.rent for f in flats), 2),
        "expenses": round(sum(f.expenses for f in flats), 2),
        "interest": [f.interest_detail for f in flats if f.interest_detail is not None],
        "interest_total": round(sum(f.interest for f in flats), 2),
        "depreciation": [
            {**_property_brief(f.property), **{k: v for k, v in f.depreciation.items() if k != "missing"}}
            for f in flats
        ],
        "expenses_so_far": ctx.is_forecast,
    }
    if out["missing"] or not flats:
        out["available"] = not out["missing"]
        if not flats and not out["missing"]:
            out.update({"tax": 0.0, "income": 0.0})
        return out
    result = _assessment({f.property.id: "marginal" for f in flats}, flats, payer)
    out.update({
        "available": True,
        "depreciation_total": round(sum(f.depreciation["amount"] for f in flats), 2),
        "income": result["taxable_income"],
        "loss": result["marginal_loss"],
        "income_tax": result["income_tax"],
        "surtax": result["surtax"],
        "capital_surtax": result["capital_surtax"],
        "tax": result["tax"],
    })
    return out


def _combination(flats: list[_Flat], payer: _Payer, result: dict) -> dict:
    """The cheapest choice of one track per flat, once every track can be computed.

    Every assignment is tried; a household large enough to need more than ``_MAX_MIX_FLATS``
    flats is past the point where renting is likely a business anyway.
    """
    out: dict = {"available": False, "reason": None}
    if len(flats) < 2:
        out["reason"] = "one_flat"
        return out
    if not result["marginal"]["available"] or result["exemption"]["ceiling"] is None:
        out["reason"] = "missing"
        return out
    if not result["ten_percent"]["available"]:
        out["reason"] = "missing"
        return out
    if len(flats) > _MAX_MIX_FLATS:
        out["reason"] = "too_many"
        return out
    choices = [
        [t for t in TRACKS if not (t == "exemption" and f.company)] for f in flats
    ]
    best = None
    for combo in product(*choices):
        tracks = {f.property.id: t for f, t in zip(flats, combo)}
        result = _assessment(tracks, flats, payer)
        if best is None or result["tax"] < best[1]["tax"] - 0.005:
            best = (combo, result)
    combo, result = best
    out.update({
        "available": True,
        "flats": [{**_property_brief(f.property), "track": t} for f, t in zip(flats, combo)],
        "mixed": len(set(combo)) > 1,
        **result,
    })
    return out


def _expense_summary(flats: list[_Flat]) -> dict:
    counted: dict = defaultdict(float)
    not_counted: dict = defaultdict(float)
    for f in flats:
        for key, amount in f.counted.items():
            counted[key] += amount
        for key, amount in f.not_counted.items():
            not_counted[key] += amount
    return {
        "counted": [{"key": k, "amount": round(v, 2)} for k, v in sorted(counted.items())],
        "not_counted": [
            {"label": label, "built_in": built_in, "amount": round(v, 2)}
            for (label, built_in), v in sorted(not_counted.items(), key=lambda x: str(x[0][0]))
        ],
        "renovation": round(sum(f.renovation for f in flats), 2),
        "recognised": sorted(RECOGNISED_EXPENSE_KEYS),
    }


def _mortgage_runs_in(mortgage, year: int) -> bool:
    start, end = date(year, 1, 1), date(year, 12, 31)
    if mortgage.end_date is not None and mortgage.end_date < start:
        return False
    return any(p.effective_from <= end for p in mortgage.payments)


def _full_month(renter: Renter, year: int, month: int) -> bool:
    """Whether the lease covers every day of the month — what the depreciation counts."""
    if renter.lease_start is None:
        return False
    first = date(year, month, 1)
    last = first + relativedelta(months=1) - relativedelta(days=1)
    if renter.lease_start > first:
        return False
    if renter.lease_end is not None and renter.lease_end <= last:
        return False
    if renter.terminated_on is not None and renter.terminated_on < last:
        return False
    return True


def _unique(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for item in items:
        key = json.dumps(item, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out

def _decoded(raw: str):
    return json.loads(raw)


def _catalog(visible: list[Property], owners: dict) -> list[dict]:
    """The people there are to pick from, each with their flats in the country.

    A company owner is listed, marked, with nothing to pick: the individual tracks do not
    apply to a company. A commercial property is listed, marked, and cannot be picked.
    """
    people: dict[str, dict] = {}
    for p in visible:
        owner = owners.get(p.property_owner_id)
        key = f"owner:{owner.id}" if owner is not None else NO_OWNER
        if key not in people:
            people[key] = {
                "key": key,
                "owner_id": owner.id if owner is not None else None,
                "name": owner.name if owner is not None else None,
                "is_company": bool(owner.is_company) if owner is not None else False,
                "properties": [],
            }
        people[key]["properties"].append(
            {**_property_brief(p), "commercial": p.type == PropertyTypeEnum.COMMERCIAL}
        )
    for person in people.values():
        person["properties"].sort(key=lambda x: (x["address"] or "").lower())
    return sorted(
        people.values(),
        key=lambda x: (x["key"] == NO_OWNER, x["is_company"], (x["name"] or "").lower()),
    )


def _property_brief(p: Property) -> dict:
    return {"id": p.id, "address": p.address, "city": p.city}

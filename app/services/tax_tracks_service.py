"""The tax tracks report (PLATFORM.md §12; plan in ``docs/תוכנית השוואת מסלולי מס בדוחות.md``).

For one tax year it groups the account's properties into households and, for each, works out
the three tracks — the exemption, 10% and the marginal rate — and the cheapest choice of one
track per flat. Every government figure comes from ``tax_values`` for that year, with its
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
  household's other income and credit points from ``tax_year_inputs``. A flat whose mortgage
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
from app.models.tax_group import TaxGroup
from app.repositories.cpi_index_repository import CpiIndexRepository
from app.repositories.property_owner_repository import PropertyOwnerRepository
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.repositories.tax_group_repository import TaxGroupRepository, member_ids
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
        groups: TaxGroupRepository,
        tax_values: TaxValueRepository,
        inputs: TaxYearInputRepository,
        cpi: CpiIndexRepository,
    ):
        self.properties = properties
        self.property_owners = property_owners
        self.renters = renters
        self.transactions = transactions
        self.groups = groups
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

    # ── Groups ───────────────────────────────────────────────────────────────

    def list_groups(self, owner_id: str) -> list[dict]:
        return [self._group_read(g) for g in self.groups.get_all(owner_id)]

    def save_group(
        self,
        owner_id: str,
        name: str,
        property_owner_ids: list[int],
        property_ids: list[int],
        group_id: int | None = None,
    ) -> dict:
        group = TaxGroup(owner_id=owner_id)
        if group_id is not None:
            group = self.groups.get_by_id(group_id, owner_id)
            if group is None:
                raise HTTPException(status_code=404, detail="Tax group not found")
        if not property_owner_ids and not property_ids:
            raise HTTPException(status_code=422, detail="A group needs an owner or a property")
        own_owners = {o.id for o in self.property_owners.get_all(owner_id, include_inactive=True)}
        own_props = {p.id for p in self.properties.get_all_by_owner(owner_id)}
        if not set(property_owner_ids) <= own_owners or not set(property_ids) <= own_props:
            raise HTTPException(status_code=400, detail="Owner or property not found")
        # One group per owner and per property, or the report would count rent twice.
        for other in self.groups.get_all(owner_id):
            if other.id == group.id:
                continue
            if set(member_ids(other.property_owner_ids)) & set(property_owner_ids) or set(
                member_ids(other.property_ids)
            ) & set(property_ids):
                raise HTTPException(
                    status_code=409, detail="An owner or property is already in another group"
                )
        return self._group_read(self.groups.save(group, name, property_owner_ids, property_ids))

    def delete_group(self, owner_id: str, group_id: int) -> bool:
        group = self.groups.get_by_id(group_id, owner_id)
        if group is None:
            return False
        self.groups.delete(group)
        self.inputs.delete_for_group(owner_id, f"custom:{group_id}")
        return True

    # ── Other income and credit points ───────────────────────────────────────

    def save_inputs(
        self,
        owner_id: str,
        group_key: str,
        year: int,
        first_year: int,
        today: date,
        other_income: float | None,
        credit_points: float | None,
    ) -> dict:
        if not first_year <= year <= today.year:
            raise HTTPException(status_code=422, detail="No tax report for that year")
        kind, _, raw_id = group_key.partition(":")
        if group_key != NO_OWNER:
            exists = False
            if raw_id.isdigit() and kind == "owner":
                exists = any(
                    o.id == int(raw_id)
                    for o in self.property_owners.get_all(owner_id, include_inactive=True)
                )
            elif raw_id.isdigit() and kind == "custom":
                exists = self.groups.get_by_id(int(raw_id), owner_id) is not None
            if not exists:
                raise HTTPException(status_code=404, detail="Tax group not found")
        row = self.inputs.save(owner_id, group_key, year, other_income, credit_points)
        return {
            "group_key": row.group_key,
            "year": row.year,
            "other_income": row.other_income,
            "credit_points": row.credit_points,
        }

    @staticmethod
    def _group_read(group: TaxGroup) -> dict:
        return {
            "id": group.id,
            "name": group.name,
            "property_owner_ids": member_ids(group.property_owner_ids),
            "property_ids": member_ids(group.property_ids),
        }

    # ── Report ───────────────────────────────────────────────────────────────

    def report(
        self,
        owner_id: str,
        account_country: str | None,
        year: int,
        today: date,
        hidden_property_ids: Iterable[int] = (),
    ) -> dict:
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
        custom = self.groups.get_all(owner_id)
        key_of = _group_keys(custom, owners)

        company_owner_ids = {oid for oid, o in owners.items() if o.is_company}
        residential = [
            p
            for p in visible
            if p.type != PropertyTypeEnum.COMMERCIAL and p.property_owner_id not in company_owner_ids
        ]
        renters = [r for r in self.renters.get_all(owner_id) if r.property_id in {p.id for p in residential}]
        ledger = self._ledger(owner_id, year, today, residential, renters)

        groups: dict[str, dict] = {}
        for g in custom:
            groups[f"custom:{g.id}"] = _empty_group(f"custom:{g.id}", "custom", g.name, g)
        for p in visible:
            if p.property_owner_id in company_owner_ids:
                continue  # listed once, with the company owners, below
            key = key_of(p)
            if key not in groups:
                kind = "none" if key == NO_OWNER else "owner"
                name = None if key == NO_OWNER else owners[p.property_owner_id].name
                groups[key] = _empty_group(key, kind, name)
            if p.type == PropertyTypeEnum.COMMERCIAL:
                groups[key]["commercial"].append(_property_brief(p))
            else:
                groups[key]["_properties"].append(p)

        renters_by_property: dict[int, list[Renter]] = defaultdict(list)
        for r in renters:
            renters_by_property[r.property_id].append(r)
        expenses: dict[int, list] = defaultdict(list)
        for tx in self.transactions.expenses_between(
            owner_id, [p.id for p in residential], date(year, 1, 1), date(year, 12, 31)
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
        out_groups = [
            self._group_result(g, ctx) for g in sorted(groups.values(), key=_group_order)
        ]

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
            "company_owners": sorted(o.name for oid, o in owners.items() if oid in company_owner_ids
                                     and any(p.property_owner_id == oid for p in visible)),
            "locked_properties": len([p for p in in_country if p.id in hidden]),
            "groups": out_groups,
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

    # ── One group ────────────────────────────────────────────────────────────

    def _group_result(self, group: dict, ctx: "_Context") -> dict:
        props: list[Property] = group.pop("_properties")
        ledger, values = ctx.ledger, ctx.values
        monthly = [round(sum(ledger.rent[p.id][m] for p in props), 2) for m in range(12)]
        forecast = [any(ledger.forecast[p.id][m] for p in props) for m in range(12)]
        gross = round(sum(monthly), 2)
        group["properties"] = [
            {**_property_brief(p), "rent": round(sum(ledger.rent[p.id]), 2)} for p in props
        ]
        group["rent_by_month"] = monthly
        group["forecast_months"] = [m + 1 for m in range(12) if forecast[m]]
        group["total_rent"] = gross
        group["checks"] = [c for p in props for c in ctx.checks.get(p.id, [])]

        # Only flats that earned rent this year are on a track; an empty one has nothing to tax.
        flats = [self._flat(p, ctx) for p in props if sum(ledger.rent[p.id]) > 0]
        row = ctx.inputs.get(group["key"])
        other = row.other_income if row is not None else None
        person = _Person.of(props, ctx)
        credits, inputs = _credits(values, other, row.credit_points if row is not None else None)
        group["inputs"] = inputs
        group["senior"] = person.senior
        payer = _Payer(values, other, credits, person.senior)

        group["exemption"] = self._exemption(flats, monthly, gross, payer, person)
        group["ten_percent"] = _ten_percent(flats, gross, payer)
        group["marginal"] = _marginal(flats, payer, person, ctx)
        group["combination"] = _combination(flats, payer, group)
        group["expenses"] = _expense_summary(flats)

        notes = list(person.notes)
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
        group["notes"] = notes
        return group

    @staticmethod
    def _exemption(flats: list["_Flat"], monthly: list[float], gross: float, payer: "_Payer", person) -> dict:
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
            # A company renter rules the exemption out for its flat. The whole-group figure
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


@dataclass
class _Person:
    """Who the group's rent is taxed as: whether the 60-and-over brackets apply."""

    senior: bool | None
    missing: list
    notes: list

    @staticmethod
    def of(props: list[Property], ctx: _Context) -> "_Person":
        age = ctx.values.get("senior_age")
        notes, statuses = [], set()
        owner_ids = {p.property_owner_id for p in props if p.property_owner_id in ctx.owners}
        for oid in sorted(owner_ids):
            owner = ctx.owners[oid]
            if owner.birth_date is not None:
                statuses.add(age is not None and ctx.year - owner.birth_date.year >= age)
            elif owner.over_60:
                marked = owner.over_60_marked_on
                if marked is not None and marked.year > ctx.year:
                    # Ticked by hand in a later year: we do not know about this one.
                    notes.append({"code": "over_60_ticked_later", "name": owner.name,
                                  "year": marked.year})
                    statuses.add(False)
                else:
                    statuses.add(True)
            else:
                statuses.add(False)
        if not owner_ids:
            notes.append({"code": "no_owner_age"})
        if len(statuses) > 1:
            # One household, two people on different brackets: the rent is someone's, and
            # the report does not split it between them.
            return _Person(None, [{"code": "mixed_ages"}], notes)
        return _Person(True in statuses, [], notes)


@dataclass
class _Payer:
    """The group's tax position for the year: what the rent is stacked on."""

    values: dict
    other_income: float | None
    credits: float | None
    senior: bool | None = None

    _NEEDED = ("personal_brackets", "other_income_brackets", "surtax_threshold",
               "surtax_rate_pct", "credit_point_monthly", "resident_credit_points", "senior_age")

    def missing(self) -> list[dict]:
        out = []
        if any(self.values.get(k) is None for k in self._NEEDED):
            out.append({"code": "tax_figures"})
        if self.other_income is None:
            out.append({"code": "other_income"})
        return out

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
        "other_income": other,
        "credit_points": stored,
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
    """The year's tax on the group's rent with each flat on the track ``tracks`` gives it.

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


def _marginal(flats: list[_Flat], payer: _Payer, person: _Person, ctx: _Context) -> dict:
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


def _combination(flats: list[_Flat], payer: _Payer, group: dict) -> dict:
    """The cheapest choice of one track per flat, once every track can be computed.

    Every assignment is tried; a group large enough to need more than ``_MAX_MIX_FLATS``
    flats is past the point where renting is likely a business anyway.
    """
    out: dict = {"available": False, "reason": None}
    if len(flats) < 2:
        out["reason"] = "one_flat"
        return out
    if not group["marginal"]["available"] or group["exemption"]["ceiling"] is None:
        out["reason"] = "missing"
        return out
    if not group["ten_percent"]["available"]:
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


def _group_keys(custom: list[TaxGroup], owners: dict):
    by_property: dict[int, TaxGroup] = {}
    by_owner: dict[int, TaxGroup] = {}
    for g in custom:
        for pid in member_ids(g.property_ids):
            by_property.setdefault(pid, g)
        for oid in member_ids(g.property_owner_ids):
            by_owner.setdefault(oid, g)

    def key_of(p: Property) -> str:
        g = by_property.get(p.id) or by_owner.get(p.property_owner_id)
        if g is not None:
            return f"custom:{g.id}"
        if p.property_owner_id in owners:
            return f"owner:{p.property_owner_id}"
        return NO_OWNER

    return key_of


def _empty_group(key: str, kind: str, name: str | None, custom: TaxGroup | None = None) -> dict:
    return {
        "key": key,
        "kind": kind,
        "name": name,
        "group_id": custom.id if custom else None,
        "property_owner_ids": member_ids(custom.property_owner_ids) if custom else [],
        "member_property_ids": member_ids(custom.property_ids) if custom else [],
        "commercial": [],
        "_properties": [],
    }


def _group_order(group: dict) -> tuple:
    rank = {"custom": 0, "owner": 1, "none": 2}[group["kind"]]
    return (rank, (group["name"] or "").lower())


def _property_brief(p: Property) -> dict:
    return {"id": p.id, "address": p.address, "city": p.city}

"""Read-only tools the Portfolio Chat Agent can call.

The agent (Claude) never computes numbers itself — it decides *which* tool to call,
and our Python code produces every amount, date, and total. Each tool here is a thin
wrapper over an existing owner-scoped service, so the agent can only ever read data
the signed-in owner already owns.

Two rules make this safe and are enforced structurally, not by trusting the model:

1. **owner_id comes from the session, never the model.** ``dispatch`` injects the
   authenticated ``owner_id`` and ignores any ``owner_id`` the model might put in the
   tool input. Every service call is scoped by it, so a model that guesses another
   owner's property id simply gets "not found".
2. **Only whitelisted tools run.** ``dispatch`` refuses any name not in
   ``TOOL_SCHEMAS``; it never getattrs arbitrary methods.

``TOOL_SCHEMAS`` is the list handed to the Anthropic Messages API as ``tools=``; each
entry's ``name`` matches a ``_tool_<name>`` method below. Tool results are returned as
compact, JSON-serialisable dicts that always include ids (so answers can cite sources)
and pre-formatted ``₪`` display strings (so the model quotes money verbatim instead of
formatting it).
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.repositories.cpi_index_repository import CpiIndexRepository, reference_period
from app.repositories.expense_category_repository import ExpenseCategoryRepository
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.repositories.supplier_repository import SupplierRepository
from app.repositories.transaction_repository import TransactionRepository
from app.services import report_service
from app.services.cpi_indexing_service import compute_chained_cpi_amount, compute_cpi_amount
from app.services.property_service import PropertyService
from app.services.renter_service import RenterService
from app.services.supplier_service import SupplierService
from app.services.transaction_service import TransactionService

# How many transaction rows a single query_transactions call will pull. Small-landlord
# portfolios sit well under this; if a query ever hits it we flag the result truncated.
_MAX_TXN_ROWS = 500


def format_shekels(amount: Optional[float | Decimal]) -> str:
    """Money the app's way: ``₪12,000`` (no decimals when whole, else two)."""
    if amount is None:
        return "₪0"
    value = Decimal(str(amount))
    if value == value.to_integral_value():
        return f"₪{int(value):,}"
    return f"₪{value:,.2f}"


def _num(amount: Optional[float | Decimal]) -> float:
    """Decimal/None → JSON-safe float."""
    return float(amount) if amount is not None else 0.0


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d is not None else None


def _parse_date(value: Any) -> Optional[date]:
    """Parse an ISO 'YYYY-MM-DD' string from the model; None passes through.
    Raises ValueError on garbage so the caller can hand the model a correction."""
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _payment_frequency(number_of_payments: Optional[int]) -> str:
    """Human label for the payment cadence stored as payments-per-year."""
    return {12: "monthly", 4: "quarterly", 1: "yearly"}.get(
        number_of_payments or 0, f"{number_of_payments}/year" if number_of_payments else "monthly"
    )


def _load_lease_years(raw: Any) -> list[dict]:
    if isinstance(raw, str):
        try:
            return json.loads(raw) or []
        except (json.JSONDecodeError, TypeError):
            return []
    return raw or []


def _current_year_index(lease_start: Optional[date], count: int, today: Optional[date] = None) -> int:
    """0-based index of the lease year in effect today (clamped to the schedule)."""
    if count <= 0:
        return 0
    if lease_start is None:
        return 0
    today = today or date.today()
    idx = 0
    for i in range(count):
        if lease_start + relativedelta(years=i) <= today:
            idx = i
    return min(idx, count - 1)


def _cell(mc: Any) -> dict:
    """MonthCell (revenue/expenses/net Decimals) → JSON dict with display strings."""
    rev, exp, net = mc.revenue, mc.expenses, mc.net
    return {
        "revenue": _num(rev),
        "expenses": _num(exp),
        "net": _num(net),
        "revenue_display": format_shekels(rev),
        "expenses_display": format_shekels(exp),
        "net_display": format_shekels(net),
    }


def _cat_map(categories: dict) -> dict:
    """category → Decimal  ==>  category → '₪…' display string."""
    return {name: format_shekels(amt) for name, amt in categories.items()}


# --- Tool schemas advertised to the model ------------------------------------------
# Descriptions are part of the prompt: they tell the model when to reach for each tool.

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "list_properties",
        "description": (
            "List all of the owner's properties with their address, type, the free-text "
            "'property owner' (e.g. a parent the unit belongs to), whether each is currently "
            "occupied, and the current renter. Use this to find property ids or answer "
            "'which/how many properties' questions."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "query_transactions",
        "description": (
            "List transactions (money in = 'revenue', money out = 'expense') and their "
            "server-computed total. Filter by type, a specific property or renter, a free-text "
            "search (matches renter/property/'property owner'/expense-category/supplier/notes), "
            "and a payment-date range. Dates filter by DATE PAID (date_of_payment). For "
            "period/category totals of RENT (which is attributed to the month it was FOR, not "
            "when it was paid) prefer get_report_summary. Returns a `total` you must quote as-is."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["revenue", "expense"],
                    "description": "Limit to money in (revenue) or out (expense). Omit for both.",
                },
                "property_id": {"type": "integer", "description": "Only this property's transactions."},
                "renter_id": {"type": "integer", "description": "Only this renter's transactions."},
                "search": {
                    "type": "string",
                    "description": "Free text; matches renter, property, property-owner, category, supplier, notes.",
                },
                "from_date": {"type": "string", "description": "Earliest payment date, ISO YYYY-MM-DD."},
                "to_date": {"type": "string", "description": "Latest payment date, ISO YYYY-MM-DD."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_property",
        "description": (
            "Full detail for one property plus its headline numbers: current renter and rent, "
            "and lifetime revenue / expenses / net. Use after list_properties to drill into one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"property_id": {"type": "integer"}},
            "required": ["property_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_renters",
        "description": (
            "List renters (each renter IS a lease): name, property, lease start and computed end, "
            "contract vs option years, the rent in effect this year, payment day/frequency, and "
            "security. Optionally limited to one property. Use for 'who rents…', lease-term, and "
            "end-date questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"property_id": {"type": "integer", "description": "Only renters of this property."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_lease_schedule",
        "description": (
            "The year-by-year rent timeline for one renter's lease: each year's type "
            "(contract/option), rent, and escalation rule, plus lease start/end and security. For "
            "the CPI calculation behind a year's rent, use explain_cpi."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"renter_id": {"type": "integer"}},
            "required": ["renter_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "explain_cpi",
        "description": (
            "Explain step by step how a CPI-linked (הצמדה למדד) renter's rent for one lease year "
            "is computed: the base rent, the frozen base index, the known index used (the "
            "published 'known index'/המדד הידוע), the ratio, whether the 'never below the floor' "
            "clause (לא יפחת) applied, and whether the year is finalized or still a projection. "
            "Use for 'why did the rent change / go up' questions on CPI leases. Every number is "
            "from the app's real CPI engine — quote them as given."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "renter_id": {"type": "integer"},
                "year": {
                    "type": "integer",
                    "description": "Lease YEAR NUMBER (1 = first year). Defaults to the year in effect now.",
                },
            },
            "required": ["renter_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_overdue",
        "description": (
            "Renters with rent not yet recorded for the current month (the same inference the app's "
            "'Needs Attention' uses: overdue = no revenue row for the period). Each row includes the "
            "amount owed and how many days overdue. Optionally scope to one 'property owner'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property_owner": {
                    "type": "string",
                    "description": "Only renters on properties with this free-text owner (e.g. 'Dad').",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_report_summary",
        "description": (
            "Yearly aggregates behind the app's reports. type='income_expense' → revenue/expenses/net "
            "per owner→property and grand total (revenue counted by the month it was FOR). "
            "type='expense_log' → expense totals per category, per property, and per owner (by payment "
            "date). Best tool for 'how much did I earn/spend', per-property net, and category totals "
            "like repairs across an owner's properties."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["income_expense", "expense_log"]},
                "year": {"type": "integer", "description": "Calendar year, e.g. 2025. Defaults to the current year."},
            },
            "required": ["type"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_suppliers",
        "description": "List the owner's suppliers (people they pay). Optional free-text search by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search": {"type": "string"},
                "include_inactive": {"type": "boolean", "description": "Include deactivated suppliers (default false)."},
            },
            "additionalProperties": False,
        },
    },
]

_TOOL_NAMES = {spec["name"] for spec in TOOL_SCHEMAS}


class AgentTools:
    """Owner-scoped, read-only tool executor. One instance per request/session."""

    def __init__(self, db: Session):
        self.db = db
        property_repo = PropertyRepository(db)
        renter_repo = RenterRepository(db)
        transaction_repo = TransactionRepository(db)
        category_repo = ExpenseCategoryRepository(db)
        supplier_repo = SupplierRepository(db)

        self.cpi_index_repository = CpiIndexRepository(db)
        self.property_service = PropertyService(property_repo, renter_repo)
        self.renter_service = RenterService(renter_repo, property_repo, self.cpi_index_repository)
        self.supplier_service = SupplierService(supplier_repo, category_repo)
        self.transaction_service = TransactionService(
            transaction_repository=transaction_repo,
            property_repository=property_repo,
            renter_repository=renter_repo,
            expense_category_repository=category_repo,
            supplier_repository=supplier_repo,
        )

    # -- dispatch -------------------------------------------------------------------
    def dispatch(self, name: str, owner_id: str, tool_input: Optional[dict]) -> dict:
        """Run tool ``name`` for ``owner_id``. owner_id is authoritative — any owner_id
        inside ``tool_input`` is ignored. Unknown tools and bad inputs return an ``error``
        dict (the model reads it and can retry) rather than raising."""
        if name not in _TOOL_NAMES:
            return {"error": f"unknown tool: {name}"}
        params = dict(tool_input or {})
        params.pop("owner_id", None)  # never let the model set the tenant
        try:
            method = getattr(self, f"_tool_{name}")
            return method(owner_id, params)
        except ValueError as exc:
            return {"error": f"invalid argument: {exc}"}
        except HTTPException as exc:
            # e.g. a renter that belongs to another owner → 403. Surface as a plain
            # "not found" so the loop continues and nothing leaks about other tenants.
            return {"error": "not found"}

    # -- tools ----------------------------------------------------------------------
    def _tool_list_properties(self, owner_id: str, params: dict) -> dict:
        properties = self.property_service.list_properties(owner_id)
        out = []
        for p in properties:
            renters = self.property_service.get_property_renters(p.id, owner_id) or []
            current = renters[0] if renters else None
            out.append(
                {
                    "id": p.id,
                    "address": p.address,
                    "city": p.city,
                    "type": p.type.value if hasattr(p.type, "value") else p.type,
                    "property_owner": p.property_owner,
                    "status": "occupied" if current else "vacant",
                    "current_renter": (
                        {
                            "id": current.id,
                            "name": f"{current.first_name} {current.last_name}".strip(),
                            "monthly_rent": _num(current.monthly_rent),
                            "monthly_rent_display": format_shekels(current.monthly_rent),
                        }
                        if current
                        else None
                    ),
                }
            )
        return {"count": len(out), "properties": out}

    def _tool_query_transactions(self, owner_id: str, params: dict) -> dict:
        type_filter = params.get("type")
        rows = self.transaction_service.list_transactions(
            owner_id=owner_id,
            type_filter=type_filter,
            property_id=params.get("property_id"),
            renter_id=params.get("renter_id"),
            q=params.get("search"),
            from_date=_parse_date(params.get("from_date")),
            to_date=_parse_date(params.get("to_date")),
            limit=_MAX_TXN_ROWS,
        )
        total = sum((Decimal(str(r.amount)) for r in rows), Decimal("0"))
        return {
            "count": len(rows),
            "truncated": len(rows) >= _MAX_TXN_ROWS,
            "total": _num(total),
            "total_display": format_shekels(total),
            "transactions": [
                {
                    "id": r.id,
                    "type": r.type.value if hasattr(r.type, "value") else r.type,
                    "date_of_payment": _iso(r.date_of_payment),
                    "month_for": _iso(r.month_for),
                    "amount": _num(r.amount),
                    "amount_display": format_shekels(r.amount),
                    "property_id": r.property_id,
                    "property_name": r.property_name,
                    "renter_id": r.renter_id,
                    "renter_name": r.renter_name,
                    "category_name": r.category_name,
                    "supplier_name": r.supplier_name,
                    "notes": r.notes,
                }
                for r in rows
            ],
        }

    def _tool_get_property(self, owner_id: str, params: dict) -> dict:
        property_id = params.get("property_id")
        p = self.property_service.get_property(property_id, owner_id)
        if p is None:
            return {"error": "not found"}
        renters = self.property_service.get_property_renters(p.id, owner_id) or []
        current = renters[0] if renters else None

        # Lifetime totals for this property, computed here (never by the model).
        txns = self.transaction_service.list_transactions(
            owner_id=owner_id, property_id=p.id, limit=_MAX_TXN_ROWS
        )
        revenue = sum(
            (Decimal(str(t.amount)) for t in txns if t.type.value == "revenue"), Decimal("0")
        )
        expenses = sum(
            (Decimal(str(t.amount)) for t in txns if t.type.value == "expense"), Decimal("0")
        )
        net = revenue - expenses
        return {
            "id": p.id,
            "address": p.address,
            "city": p.city,
            "type": p.type.value if hasattr(p.type, "value") else p.type,
            "property_owner": p.property_owner,
            "number_of_rooms": p.number_of_rooms,
            "floor": p.floor,
            "apartment": p.apartment,
            "purchase_price": _num(p.purchase_price),
            "purchase_price_display": format_shekels(p.purchase_price),
            "property_tax": _num(p.property_tax) if p.property_tax is not None else None,
            "house_committee": _num(p.house_committee) if p.house_committee is not None else None,
            "status": "occupied" if current else "vacant",
            "current_renter": (
                {
                    "id": current.id,
                    "name": f"{current.first_name} {current.last_name}".strip(),
                    "monthly_rent": _num(current.monthly_rent),
                    "monthly_rent_display": format_shekels(current.monthly_rent),
                }
                if current
                else None
            ),
            "totals": {
                "revenue": _num(revenue),
                "expenses": _num(expenses),
                "net": _num(net),
                "revenue_display": format_shekels(revenue),
                "expenses_display": format_shekels(expenses),
                "net_display": format_shekels(net),
            },
        }

    def _tool_list_renters(self, owner_id: str, params: dict) -> dict:
        property_id = params.get("property_id")
        renters = self.renter_service.list_renters(owner_id)
        out = []
        for r in renters:
            if property_id is not None and r.property_id != property_id:
                continue
            years = _load_lease_years(r.lease_years)
            idx = _current_year_index(r.lease_start, len(years))
            current_amount = years[idx]["amount"] if years else None
            out.append(
                {
                    "id": r.id,
                    "name": f"{r.first_name} {r.last_name}".strip(),
                    "property_id": r.property_id,
                    "property_address": (
                        f"{r.property.address}, {r.property.city}" if r.property else None
                    ),
                    "lease_start": _iso(r.lease_start),
                    "lease_end": _iso(r.lease_end),
                    "contract_term_years": r.contract_term_years,
                    "option_years": r.option_years,
                    "current_monthly_rent": _num(current_amount),
                    "current_monthly_rent_display": format_shekels(current_amount),
                    "payment_day_of_month": r.payment_day_of_month,
                    "payment_frequency": _payment_frequency(r.number_of_payments),
                    "rent_escalation_mode": r.rent_escalation_mode,
                    "insurance_type": r.insurance_type,
                    "insurance_amount": _num(r.insurance_amount) if r.insurance_amount is not None else None,
                }
            )
        return {"count": len(out), "renters": out}

    def _tool_get_lease_schedule(self, owner_id: str, params: dict) -> dict:
        r = self.renter_service.get_renter(params.get("renter_id"), owner_id)
        if r is None:
            return {"error": "not found"}
        years = _load_lease_years(r.lease_years)
        cpi_linked = r.rent_escalation_mode == "cpi" or any(
            isinstance(y.get("rule"), dict) and y["rule"].get("mode") == "cpi" for y in years
        )
        schedule = []
        for i, y in enumerate(years):
            starts = r.lease_start + relativedelta(years=i) if r.lease_start else None
            ends = r.lease_start + relativedelta(years=i + 1) if r.lease_start else None
            schedule.append(
                {
                    "year_number": i + 1,
                    "type": y.get("type"),
                    "starts": _iso(starts),
                    "ends": _iso(ends),
                    "amount": _num(y.get("amount")),
                    "amount_display": format_shekels(y.get("amount")),
                    "rule": y.get("rule"),
                }
            )
        result = {
            "renter_id": r.id,
            "name": f"{r.first_name} {r.last_name}".strip(),
            "property_id": r.property_id,
            "lease_start": _iso(r.lease_start),
            "lease_end": _iso(r.lease_end),
            "contract_term_years": r.contract_term_years,
            "option_years": r.option_years,
            "rent_escalation_mode": r.rent_escalation_mode,
            "base_rent": _num(r.base_rent) if r.base_rent is not None else None,
            "payment_day_of_month": r.payment_day_of_month,
            "payment_frequency": _payment_frequency(r.number_of_payments),
            "insurance_type": r.insurance_type,
            "insurance_amount": _num(r.insurance_amount) if r.insurance_amount is not None else None,
            "cpi_linked": cpi_linked,
            "years": schedule,
        }
        if cpi_linked:
            result["note"] = "For the CPI calculation (base index, floor, projected vs finalized) call explain_cpi."
        return result

    def _index_reading(self, d: date) -> tuple[Optional[dict], Optional[tuple[int, int]]]:
        """The CPI reading actually applied for date ``d``: ({value, month}, (year, month))
        or (None, None) if none is cached."""
        row = self.cpi_index_repository.reading_on_or_before(settings.CPI_INDEX_ID, d)
        if row is None:
            return None, None
        return {"value": row.value, "month": f"{row.year:04d}-{row.month:02d}"}, (row.year, row.month)

    def _tool_explain_cpi(self, owner_id: str, params: dict) -> dict:
        r = self.renter_service.get_renter(params.get("renter_id"), owner_id)
        if r is None:
            return {"error": "not found"}
        years = _load_lease_years(r.lease_years)
        if not years or not r.lease_start:
            return {"error": "this lease has no dated year schedule to explain"}

        mode = r.rent_escalation_mode
        base_rent = r.base_rent if r.base_rent else years[0]["amount"]

        requested = params.get("year")
        if requested is None:
            idx = _current_year_index(r.lease_start, len(years))
        else:
            idx = int(requested) - 1
            if idx < 0 or idx >= len(years):
                return {"error": f"year must be between 1 and {len(years)}"}

        year_row = years[idx]
        rule = year_row.get("rule") if isinstance(year_row.get("rule"), dict) else None
        anniversary = r.lease_start + relativedelta(years=idx)

        result: dict = {
            "renter_id": r.id,
            "name": f"{r.first_name} {r.last_name}".strip(),
            "escalation_mode": mode,
            "year_number": idx + 1,
            "year_type": year_row.get("type"),
            "year_starts": _iso(anniversary),
        }

        year_is_cpi = mode == "cpi" or (rule is not None and rule.get("mode") == "cpi")
        if not year_is_cpi:
            result["cpi_linked"] = False
            result["amount"] = _num(year_row.get("amount"))
            result["amount_display"] = format_shekels(year_row.get("amount"))
            result["message"] = (
                f"Year {idx + 1} of this lease is not CPI-linked; its rent follows the "
                f"'{rule.get('mode') if rule else mode}' rule, not index linkage."
            )
            return result

        known_dict, known_period = self._index_reading(anniversary)
        known_index = known_dict["value"] if known_dict else None
        # A year is finalized once the exact known-index month for its anniversary is
        # published; until then an older reading stands in and the amount is a projection.
        finalized = known_period is not None and known_period == reference_period(anniversary)

        result.update(
            {
                "cpi_linked": True,
                "base_rent": _num(base_rent),
                "base_rent_display": format_shekels(base_rent),
                "known_index": known_dict,
                "known_index_status": "finalized" if finalized else "projected",
                "floor_note": "Rent never falls below its floor (the לא יפחת clause).",
            }
        )

        if mode == "cpi":
            # Whole-lease linkage: every year measured against the base index frozen at signing.
            base_index = r.cpi_base_index
            base_year, base_month = reference_period(r.lease_start)
            amount = compute_cpi_amount(base_rent, base_index, known_index)
            ratio = (known_index / base_index) if (base_index and known_index) else None
            floor_applied = bool(base_index and known_index and known_index < base_index)
            prev_amount = years[idx - 1].get("amount") if idx > 0 else round(base_rent)
            result.update(
                {
                    "linkage": "whole_lease_fixed_base",
                    "formula": "rent = base_rent × max(known_index ÷ base_index, 1)",
                    "base_index": (
                        {"value": base_index, "month": f"{base_year:04d}-{base_month:02d}"}
                        if base_index
                        else None
                    ),
                    "ratio": round(ratio, 6) if ratio is not None else None,
                    "floor_applied": floor_applied,
                    "previous_year_amount": _num(prev_amount),
                    "previous_year_amount_display": format_shekels(prev_amount),
                    "amount": _num(amount),
                    "amount_display": format_shekels(amount),
                }
            )
        else:
            # Chained (custom per-year rule): measured against the PREVIOUS year's amount and index.
            prev_idx = max(idx - 1, 0)
            prev_amount = years[prev_idx].get("amount") if idx > 0 else round(base_rent)
            prev_dict, _ = self._index_reading(r.lease_start + relativedelta(years=prev_idx))
            prev_index = prev_dict["value"] if prev_dict else None
            amount = compute_chained_cpi_amount(prev_amount, prev_index, known_index)
            ratio = (known_index / prev_index) if (prev_index and known_index) else None
            floor_applied = bool(prev_index and known_index and known_index < prev_index)
            result.update(
                {
                    "linkage": "chained_per_year",
                    "formula": "rent = previous_year_rent × max(known_index ÷ previous_year_index, 1)",
                    "previous_year_amount": _num(prev_amount),
                    "previous_year_amount_display": format_shekels(prev_amount),
                    "previous_year_index": prev_dict,
                    "ratio": round(ratio, 6) if ratio is not None else None,
                    "floor_applied": floor_applied,
                    "amount": _num(amount),
                    "amount_display": format_shekels(amount),
                }
            )
        return result

    def _tool_get_overdue(self, owner_id: str, params: dict) -> dict:
        overdue = self.renter_service.get_overdue_this_month(
            owner_id, property_owner=params.get("property_owner")
        )
        return {
            "count": len(overdue),
            "renters": [
                {
                    "renter_id": o.renter_id,
                    "name": f"{o.first_name} {o.last_name}".strip(),
                    "property_id": o.property_id,
                    "property_address": o.property_address,
                    "property_owner": o.property_owner,
                    "amount_owed": _num(o.monthly_amount),
                    "amount_owed_display": format_shekels(o.monthly_amount),
                    "payment_day_of_month": o.payment_day_of_month,
                    "days_overdue": o.days_overdue,
                }
                for o in overdue
            ],
        }

    def _tool_get_report_summary(self, owner_id: str, params: dict) -> dict:
        report_type = params.get("type")
        year = params.get("year") or date.today().year
        if report_type == "income_expense":
            data = report_service.get_income_expense_data(self.db, owner_id, year)
            return {
                "report": "income_expense",
                "year": year,
                "grand_total": _cell(data.grand_total),
                "owners": [
                    {
                        "owner_name": o.owner_name or "(No Owner)",
                        "total": _cell(o.total),
                        "properties": [
                            {"property_address": p.property_address, **_cell(p.total)}
                            for p in o.properties
                        ],
                    }
                    for o in data.owners
                ],
            }
        if report_type == "expense_log":
            data = report_service.get_expense_log_data(self.db, owner_id, year)
            return {
                "report": "expense_log",
                "year": year,
                "grand_total": format_shekels(data.grand_total),
                "categories": data.categories,
                "grand_total_by_category": _cat_map(data.grand_total_by_category),
                "owners": [
                    {
                        "owner_name": o.owner_name or "(No Owner)",
                        "total": format_shekels(o.total),
                        "categories": _cat_map(o.categories),
                        "properties": [
                            {
                                "property_address": p.property_address,
                                "total": format_shekels(p.total),
                                "categories": _cat_map(p.categories),
                            }
                            for p in o.properties
                        ],
                    }
                    for o in data.owners
                ],
            }
        return {"error": "type must be 'income_expense' or 'expense_log'"}

    def _tool_list_suppliers(self, owner_id: str, params: dict) -> dict:
        suppliers = self.supplier_service.list_suppliers(
            owner_id,
            q=params.get("search"),
            include_inactive=bool(params.get("include_inactive", False)),
        )
        return {
            "count": len(suppliers),
            "suppliers": [
                {
                    "id": s.id,
                    "name": s.name,
                    "phone": s.phone,
                    "category_ids": s.category_ids,
                    "is_active": s.is_active,
                }
                for s in suppliers
            ],
        }

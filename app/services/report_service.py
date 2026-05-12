import csv
import io
from collections import defaultdict
from decimal import Decimal

from fpdf import FPDF
from sqlalchemy import extract, select
from sqlalchemy.orm import Session, selectinload

from app.models.transaction import Transaction, TransactionTypeEnum
from app.schemas.report import (
    ExpenseLogReportResponse,
    ExpenseLogRow,
    IncomeExpenseReportResponse,
    MonthCell,
    OwnerIncomeData,
    OwnerPivotData,
    PropertyIncomeData,
    PropertyPivotData,
)

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _fmt(amount: Decimal) -> str:
    return f"ILS {amount:,.0f}"


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------

def get_income_expense_data(db: Session, owner_id: str, year: int) -> IncomeExpenseReportResponse:
    from app.models.transaction import TransactionTypeEnum

    stmt = (
        select(Transaction)
        .where(Transaction.owner_id == owner_id)
        .options(selectinload(Transaction.property))
    )
    rows = list(db.scalars(stmt).all())

    # Filter to year: revenue uses month_for, expense uses date_of_payment
    filtered: list[Transaction] = []
    for t in rows:
        if t.type == TransactionTypeEnum.REVENUE:
            ref_date = t.month_for or t.date_of_payment
        else:
            ref_date = t.date_of_payment
        if ref_date and ref_date.year == year:
            filtered.append(t)

    # owner_name → property_address → month (1-12) → {revenue, expenses}
    tree: dict[str, dict[str, dict[int, dict[str, Decimal]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: {"revenue": Decimal("0"), "expenses": Decimal("0")}))
    )
    owner_order: list[str] = []
    prop_order: dict[str, list[str]] = defaultdict(list)

    for t in filtered:
        prop_owner = ""
        prop_addr = t.property_address or ""
        if t.property:
            prop_owner = t.property.property_owner or ""
            prop_addr = f"{t.property.address}, {t.property.city}" if t.property.city else t.property.address

        if t.type == TransactionTypeEnum.REVENUE:
            ref_date = t.month_for or t.date_of_payment
            month = ref_date.month
            tree[prop_owner][prop_addr][month]["revenue"] += Decimal(str(t.amount))
        else:
            month = t.date_of_payment.month
            tree[prop_owner][prop_addr][month]["expenses"] += Decimal(str(t.amount))

        if prop_owner not in owner_order:
            owner_order.append(prop_owner)
        if prop_addr not in prop_order[prop_owner]:
            prop_order[prop_owner].append(prop_addr)

    grand_revenue = Decimal("0")
    grand_expenses = Decimal("0")
    owners_out: list[OwnerIncomeData] = []

    for owner_name in owner_order:
        owner_rev = Decimal("0")
        owner_exp = Decimal("0")
        props_out: list[PropertyIncomeData] = []

        for prop_addr in prop_order[owner_name]:
            months_data = tree[owner_name][prop_addr]
            prop_rev = Decimal("0")
            prop_exp = Decimal("0")
            months_out: dict[int, MonthCell] = {}

            for m in range(1, 13):
                cell = months_data.get(m, {"revenue": Decimal("0"), "expenses": Decimal("0")})
                rev = cell["revenue"]
                exp = cell["expenses"]
                months_out[m] = MonthCell(revenue=rev, expenses=exp, net=rev - exp)
                prop_rev += rev
                prop_exp += exp

            props_out.append(PropertyIncomeData(
                property_address=prop_addr,
                months=months_out,
                total=MonthCell(revenue=prop_rev, expenses=prop_exp, net=prop_rev - prop_exp),
            ))
            owner_rev += prop_rev
            owner_exp += prop_exp

        owners_out.append(OwnerIncomeData(
            owner_name=owner_name,
            properties=props_out,
            total=MonthCell(revenue=owner_rev, expenses=owner_exp, net=owner_rev - owner_exp),
        ))
        grand_revenue += owner_rev
        grand_expenses += owner_exp

    return IncomeExpenseReportResponse(
        year=year,
        owners=owners_out,
        grand_total=MonthCell(revenue=grand_revenue, expenses=grand_expenses, net=grand_revenue - grand_expenses),
    )


def get_expense_log_data(db: Session, owner_id: str, year: int) -> ExpenseLogReportResponse:
    stmt = (
        select(Transaction)
        .where(
            Transaction.owner_id == owner_id,
            Transaction.type == TransactionTypeEnum.EXPENSE,
            extract("year", Transaction.date_of_payment) == year,
        )
        .options(
            selectinload(Transaction.property),
            selectinload(Transaction.category),
            selectinload(Transaction.supplier),
        )
        .order_by(Transaction.date_of_payment)
    )
    transactions = list(db.scalars(stmt).all())

    rows_out: list[ExpenseLogRow] = []
    # owner_name → property_address → category_name → amount
    pivot: dict[str, dict[str, dict[str, Decimal]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    )
    owner_order: list[str] = []
    prop_order: dict[str, list[str]] = defaultdict(list)
    all_categories: list[str] = []

    for t in transactions:
        prop_owner = ""
        prop_addr = t.property_address or ""
        if t.property:
            prop_owner = t.property.property_owner or ""
            prop_addr = f"{t.property.address}, {t.property.city}" if t.property.city else t.property.address

        cat_name = ""
        if t.category:
            cat_name = t.category.key or t.category.name or ""

        supplier_name = t.supplier.name if t.supplier else ""
        payment_method = t.payment_method.value if t.payment_method else ""

        rows_out.append(ExpenseLogRow(
            date=t.date_of_payment.strftime("%Y-%m-%d"),
            property_address=prop_addr,
            property_owner=prop_owner,
            category_name=cat_name,
            supplier_name=supplier_name or "",
            payment_method=payment_method,
            amount=Decimal(str(t.amount)),
            notes=t.notes or "",
        ))

        pivot[prop_owner][prop_addr][cat_name] += Decimal(str(t.amount))

        if prop_owner not in owner_order:
            owner_order.append(prop_owner)
        if prop_addr not in prop_order[prop_owner]:
            prop_order[prop_owner].append(prop_addr)
        if cat_name and cat_name not in all_categories:
            all_categories.append(cat_name)

    owners_out: list[OwnerPivotData] = []
    grand_total = Decimal("0")
    grand_by_cat: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    for owner_name in owner_order:
        owner_total = Decimal("0")
        owner_by_cat: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        props_out: list[PropertyPivotData] = []

        for prop_addr in prop_order[owner_name]:
            cat_data = pivot[owner_name][prop_addr]
            prop_total = sum(cat_data.values(), Decimal("0"))
            props_out.append(PropertyPivotData(
                property_address=prop_addr,
                categories=dict(cat_data),
                total=prop_total,
            ))
            owner_total += prop_total
            for cat, amt in cat_data.items():
                owner_by_cat[cat] += amt
                grand_by_cat[cat] += amt

        owners_out.append(OwnerPivotData(
            owner_name=owner_name,
            properties=props_out,
            categories=dict(owner_by_cat),
            total=owner_total,
        ))
        grand_total += owner_total

    return ExpenseLogReportResponse(
        year=year,
        rows=rows_out,
        owners=owners_out,
        categories=all_categories,
        grand_total_by_category=dict(grand_by_cat),
        grand_total=grand_total,
    )


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

class _PDF(FPDF):
    def __init__(self, title: str, year: int, **kwargs):
        super().__init__(**kwargs)
        self._title = title
        self._year = year

    def header(self):
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 8, f"{self._title} - {self._year}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.cell(0, 6, f"Page {self.page_no()}", align="C")


def generate_income_expense_pdf(data: IncomeExpenseReportResponse) -> bytes:
    pdf = _PDF("Income & Expense Report", data.year, orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    COL_W = 22  # width for each revenue/expense/net group
    MONTH_W = 20
    LABEL_W = 40

    def draw_section_header(label: str):
        pdf.set_fill_color(220, 230, 245)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 7, label, border=1, fill=True, new_x="LMARGIN", new_y="NEXT")

    def draw_month_table(properties: list[PropertyIncomeData], show_owner_total: bool = False):
        # Column header row
        pdf.set_fill_color(240, 240, 240)
        pdf.set_font("Helvetica", "B", 7)
        pdf.cell(MONTH_W, 6, "Month", border=1, fill=True)
        for prop in properties:
            label = prop.property_address[:22]
            pdf.cell(COL_W, 6, label, border=1, fill=True)
        if show_owner_total:
            pdf.cell(COL_W, 6, "Owner Total", border=1, fill=True)
        pdf.ln()

        # Sub-header: Revenue / Expenses / Net
        pdf.cell(MONTH_W, 5, "", border="LRB")
        for _ in properties:
            pdf.set_font("Helvetica", "", 6)
            w3 = COL_W // 3
            pdf.cell(w3, 5, "Rev", border=1, align="C")
            pdf.cell(w3, 5, "Exp", border=1, align="C")
            pdf.cell(w3, 5, "Net", border=1, align="C")
        if show_owner_total:
            w3 = COL_W // 3
            pdf.cell(w3, 5, "Rev", border=1, align="C")
            pdf.cell(w3, 5, "Exp", border=1, align="C")
            pdf.cell(w3, 5, "Net", border=1, align="C")
        pdf.ln()

        # Data rows
        for m in range(1, 13):
            pdf.set_font("Helvetica", "", 7)
            pdf.cell(MONTH_W, 5, MONTH_NAMES[m - 1][:3], border=1)
            for prop in properties:
                cell = prop.months.get(m, MonthCell())
                w3 = COL_W // 3
                pdf.set_text_color(0, 128, 0)
                pdf.cell(w3, 5, f"{cell.revenue:,.0f}", border=1, align="R")
                pdf.set_text_color(200, 0, 0)
                pdf.cell(w3, 5, f"{cell.expenses:,.0f}", border=1, align="R")
                net_color = (0, 128, 0) if cell.net >= 0 else (200, 0, 0)
                pdf.set_text_color(*net_color)
                pdf.cell(w3, 5, f"{cell.net:,.0f}", border=1, align="R")
                pdf.set_text_color(0, 0, 0)
            if show_owner_total:
                owner_rev = sum(p.months.get(m, MonthCell()).revenue for p in properties)
                owner_exp = sum(p.months.get(m, MonthCell()).expenses for p in properties)
                owner_net = owner_rev - owner_exp
                w3 = COL_W // 3
                pdf.set_text_color(0, 128, 0)
                pdf.cell(w3, 5, f"{owner_rev:,.0f}", border=1, align="R")
                pdf.set_text_color(200, 0, 0)
                pdf.cell(w3, 5, f"{owner_exp:,.0f}", border=1, align="R")
                net_color = (0, 128, 0) if owner_net >= 0 else (200, 0, 0)
                pdf.set_text_color(*net_color)
                pdf.cell(w3, 5, f"{owner_net:,.0f}", border=1, align="R")
                pdf.set_text_color(0, 0, 0)
            pdf.ln()

        # Total row
        pdf.set_fill_color(230, 240, 230)
        pdf.set_font("Helvetica", "B", 7)
        pdf.cell(MONTH_W, 5, "TOTAL", border=1, fill=True)
        for prop in properties:
            w3 = COL_W // 3
            pdf.set_text_color(0, 128, 0)
            pdf.cell(w3, 5, f"{prop.total.revenue:,.0f}", border=1, fill=True, align="R")
            pdf.set_text_color(200, 0, 0)
            pdf.cell(w3, 5, f"{prop.total.expenses:,.0f}", border=1, fill=True, align="R")
            net_color = (0, 128, 0) if prop.total.net >= 0 else (200, 0, 0)
            pdf.set_text_color(*net_color)
            pdf.cell(w3, 5, f"{prop.total.net:,.0f}", border=1, fill=True, align="R")
            pdf.set_text_color(0, 0, 0)
        if show_owner_total:
            pass  # already included in totals above if needed
        pdf.ln()

    for owner in data.owners:
        owner_label = owner.owner_name if owner.owner_name else "(No Owner)"
        draw_section_header(f"Owner: {owner_label}")
        draw_month_table(owner.properties, show_owner_total=len(owner.properties) > 1)
        pdf.ln(3)

    # Grand total summary
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(200, 220, 200)
    pdf.cell(60, 7, "GRAND TOTAL", border=1, fill=True)
    pdf.cell(40, 7, f"Revenue: {_fmt(data.grand_total.revenue)}", border=1, fill=True)
    pdf.cell(40, 7, f"Expenses: {_fmt(data.grand_total.expenses)}", border=1, fill=True)
    net_color = (0, 100, 0) if data.grand_total.net >= 0 else (180, 0, 0)
    pdf.set_text_color(*net_color)
    pdf.cell(50, 7, f"Net: {_fmt(data.grand_total.net)}", border=1, fill=True)
    pdf.set_text_color(0, 0, 0)

    return bytes(pdf.output())


def generate_expense_log_pdf(data: ExpenseLogReportResponse) -> bytes:
    pdf = _PDF("Expense Log", data.year, orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Part 1: transaction list
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(220, 220, 220)
    headers = ["Date", "Property", "Category", "Supplier", "Method", "Amount", "Notes"]
    widths =  [22,      70,         30,          40,          22,       22,       60]
    for h, w in zip(headers, widths):
        pdf.cell(w, 6, h, border=1, fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 7)
    for row in data.rows:
        values = [
            row.date,
            row.property_address[:35],
            row.category_name[:18],
            row.supplier_name[:22],
            row.payment_method,
            f"{row.amount:,.0f}",
            row.notes[:30],
        ]
        for val, w in zip(values, widths):
            pdf.cell(w, 5, val, border=1)
        pdf.ln()

    pdf.ln(6)

    # Part 2: pivot summary
    if data.owners:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, "Summary by Category & Property", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        CAT_W = 40
        PROP_W = 30
        TOT_W = 28

        # Build flat ordered columns: (owner_name, prop_addr)
        columns: list[tuple[str, str]] = []
        for owner in data.owners:
            for prop in owner.properties:
                columns.append((owner.owner_name, prop.property_address))

        # Header row 1: owner names (spanning their properties)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_fill_color(220, 230, 245)
        pdf.cell(CAT_W, 6, "Category", border=1, fill=True)

        owner_spans: list[tuple[str, int]] = []
        for owner in data.owners:
            n = len(owner.properties)
            owner_spans.append((owner.owner_name or "(No Owner)", n))

        for owner_name, n in owner_spans:
            pdf.cell(PROP_W * n, 6, owner_name[:28], border=1, fill=True)
        pdf.cell(TOT_W, 6, "Grand Total", border=1, fill=True)
        pdf.ln()

        # Header row 2: property addresses
        pdf.set_font("Helvetica", "", 6)
        pdf.set_fill_color(240, 240, 240)
        pdf.cell(CAT_W, 5, "", border=1, fill=True)
        for _, prop_addr in columns:
            pdf.cell(PROP_W, 5, prop_addr[:18], border=1, fill=True)
        pdf.cell(TOT_W, 5, "", border=1, fill=True)
        pdf.ln()

        # Data rows
        prop_map: dict[tuple[str, str], dict[str, Decimal]] = {}
        for owner in data.owners:
            for prop in owner.properties:
                prop_map[(owner.owner_name, prop.property_address)] = prop.categories

        for cat in data.categories:
            pdf.set_font("Helvetica", "", 7)
            pdf.cell(CAT_W, 5, cat, border=1)
            row_total = Decimal("0")
            for col in columns:
                amt = prop_map.get(col, {}).get(cat, Decimal("0"))
                row_total += amt
                pdf.cell(PROP_W, 5, f"{amt:,.0f}" if amt else "", border=1, align="R")
            pdf.cell(TOT_W, 5, f"{row_total:,.0f}", border=1, align="R")
            pdf.ln()

        # Total row
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_fill_color(230, 240, 230)
        pdf.cell(CAT_W, 5, "TOTAL", border=1, fill=True)
        for col in columns:
            owner_name, prop_addr = col
            prop_total = sum(
                p.total for owner in data.owners
                for p in owner.properties
                if owner.owner_name == owner_name and p.property_address == prop_addr
            )
            pdf.cell(PROP_W, 5, f"{prop_total:,.0f}", border=1, fill=True, align="R")
        pdf.cell(TOT_W, 5, f"{data.grand_total:,.0f}", border=1, fill=True, align="R")
        pdf.ln()

    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# CSV generation
# ---------------------------------------------------------------------------

def generate_income_expense_csv(data: IncomeExpenseReportResponse) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow([f"Income & Expense Report - {data.year}"])
    writer.writerow([])
    writer.writerow(["Owner", "Property", "Month", "Revenue", "Expenses", "Net"])

    for owner in data.owners:
        owner_name = owner.owner_name or "(No Owner)"
        for prop in owner.properties:
            for m in range(1, 13):
                cell = prop.months.get(m, MonthCell())
                if cell.revenue or cell.expenses:
                    writer.writerow([
                        owner_name,
                        prop.property_address,
                        MONTH_NAMES[m - 1],
                        float(cell.revenue),
                        float(cell.expenses),
                        float(cell.net),
                    ])
            writer.writerow([
                owner_name,
                prop.property_address,
                "TOTAL",
                float(prop.total.revenue),
                float(prop.total.expenses),
                float(prop.total.net),
            ])
        writer.writerow([
            owner_name, "OWNER TOTAL", "",
            float(owner.total.revenue),
            float(owner.total.expenses),
            float(owner.total.net),
        ])
        writer.writerow([])

    writer.writerow([
        "GRAND TOTAL", "", "",
        float(data.grand_total.revenue),
        float(data.grand_total.expenses),
        float(data.grand_total.net),
    ])

    return buf.getvalue()


def generate_expense_log_csv(data: ExpenseLogReportResponse) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow([f"Expense Log - {data.year}"])
    writer.writerow([])

    # Part 1: transaction list
    writer.writerow(["Date", "Property", "Owner", "Category", "Supplier", "Payment Method", "Amount", "Notes"])
    for row in data.rows:
        writer.writerow([
            row.date,
            row.property_address,
            row.property_owner,
            row.category_name,
            row.supplier_name,
            row.payment_method,
            float(row.amount),
            row.notes,
        ])

    writer.writerow([])
    writer.writerow([])

    # Part 2: pivot summary
    writer.writerow(["Summary by Category & Property"])
    writer.writerow([])

    # Build column list
    columns: list[tuple[str, str]] = []
    for owner in data.owners:
        for prop in owner.properties:
            columns.append((owner.owner_name or "(No Owner)", prop.property_address))

    # Header rows
    owner_header = ["Category"] + [col[0] for col in columns] + ["Grand Total"]
    prop_header = [""] + [col[1] for col in columns] + [""]
    writer.writerow(owner_header)
    writer.writerow(prop_header)

    prop_map: dict[tuple[str, str], dict[str, Decimal]] = {}
    for owner in data.owners:
        for prop in owner.properties:
            key = (owner.owner_name or "(No Owner)", prop.property_address)
            prop_map[key] = prop.categories

    for cat in data.categories:
        row_total = Decimal("0")
        cells = []
        for col in columns:
            amt = prop_map.get(col, {}).get(cat, Decimal("0"))
            row_total += amt
            cells.append(float(amt))
        writer.writerow([cat] + cells + [float(row_total)])

    # Total row
    totals = []
    for col in columns:
        owner_name, prop_addr = col
        prop_total = sum(
            p.total for owner in data.owners
            for p in owner.properties
            if (owner.owner_name or "(No Owner)") == owner_name and p.property_address == prop_addr
        )
        totals.append(float(prop_total))
    writer.writerow(["TOTAL"] + totals + [float(data.grand_total)])

    return buf.getvalue()

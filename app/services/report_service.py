import csv
import io
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from bidi.algorithm import get_display
from fpdf import FPDF
from sqlalchemy import and_, extract, func, or_, select
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

# --- Report languages ------------------------------------------------------
# A report is rendered in the language the client asks for (`?lang=`), independent of the data
# it contains: Hebrew data has always rendered correctly, but the chrome was English-only and
# the tables ran left-to-right regardless. Hebrew wording here is taken from the apps' own
# locale files so the report says the same thing the screen does.

DEFAULT_LANG = "en"
SUPPORTED_LANGS = ("en", "he")

MONTH_NAMES_BY_LANG = {
    "en": MONTH_NAMES,
    "he": ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
           "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"],
}

MONTH_ABBR = {
    "en": [name[:3] for name in MONTH_NAMES],
    # Deliberately no geresh (׳): the bundled Hebrew font has no glyph for it.
    "he": ["ינו", "פבר", "מרץ", "אפר", "מאי", "יונ",
           "יול", "אוג", "ספט", "אוק", "נוב", "דצמ"],
}

UI_TEXT = {
    "en": {
        "income_title": "Income & Expense Report",
        "expense_title": "Expense Log",
        "property": "Property",
        "total": "Total",
        "revenue": "Revenue",
        "expenses": "Expenses",
        "net": "Net",
        "owner": "Owner",
        "no_owner": "(No Owner)",
        "owner_total_net": "OWNER TOTAL (net)",
        "owner_total": "Owner total",
        "grand_total": "GRAND TOTAL",
        "total_row": "TOTAL",
        "page": "Page",
        "date": "Date",
        "category": "Category",
        "supplier": "Supplier",
        "method": "Method",
        "amount": "Amount",
        "notes": "Notes",
        "summary_title": "Summary by Category & Property",
        "multi_note": (
            "An expense with several categories is counted under the first; the list above "
            "shows all of them."
        ),
        "currency": "ILS ",
    },
    "he": {
        "income_title": "דוח הכנסות והוצאות",
        "expense_title": "יומן הוצאות",
        "property": "נכס",
        "total": "סה״כ",
        "revenue": "הכנסות",
        "expenses": "הוצאות",
        "net": "נטו",
        "owner": "בעלים",
        "no_owner": "(ללא בעלים)",
        "owner_total_net": "סה״כ בעלים (נטו)",
        "owner_total": "סה״כ בעלים",
        "grand_total": "סה״כ כללי",
        "total_row": "סה״כ",
        "page": "עמוד",
        "date": "תאריך",
        "category": "קטגוריה",
        "supplier": "ספק",
        "method": "אמצעי תשלום",
        "amount": "סכום",
        "notes": "הערות",
        "summary_title": "סיכום לפי קטגוריה ונכס",
        "multi_note": "הוצאה עם כמה קטגוריות נספרת תחת הראשונה; הרשימה שלמעלה מציגה את כולן.",
        "currency": "₪",
    },
}

# Built-in expense categories are stored by `key` and translated in the clients
# (`expenseCategories` in each locale file); only the user's own categories carry a `name`.
# Without this a report prints `property_tax` where both apps show "Property tax" / "ארנונה".
CATEGORY_LABELS = {
    "en": {
        "maintenance": "Maintenance",
        "electricity": "Electricity",
        "water": "Water",
        "gas": "Gas",
        "insurance": "Insurance",
        "property_tax": "Property tax",
        "repairs": "Repairs",
        "cleaning": "Cleaning",
        "gardening": "Gardening",
        "air_conditioning": "Air conditioning",
        "management_fee": "Management fee",
        "other": "Other",
    },
    "he": {
        "maintenance": "תחזוקה",
        "electricity": "חשמל",
        "water": "מים",
        "gas": "גז",
        "insurance": "ביטוח",
        "property_tax": "ארנונה",
        "repairs": "תיקונים",
        "cleaning": "ניקיון",
        "gardening": "גינון",
        "air_conditioning": "מיזוג אוויר",
        "management_fee": "דמי ניהול",
        "other": "אחר",
    },
}

PAYMENT_METHOD_LABELS = {
    "en": {
        "bit": "Bit",
        "cash": "Cash",
        "bank_transfer": "Bank transfer",
        "check": "Check",
    },
    "he": {
        "bit": "ביט",
        "cash": "מזומן",
        "bank_transfer": "העברה בנקאית",
        "check": "צ'ק",
    },
}

# Expenses with no category still count towards every total, so they need a column of their own
# — without one the category columns silently fail to add up to the total beside them.
UNCATEGORISED = "(Uncategorised)"
UNCATEGORISED_BY_LANG = {"en": UNCATEGORISED, "he": "(ללא קטגוריה)"}


def normalise_lang(lang: str | None) -> str:
    return lang if lang in SUPPORTED_LANGS else DEFAULT_LANG


def _t(lang: str, key: str) -> str:
    return UI_TEXT[normalise_lang(lang)][key]

def _category_label(category, lang: str = DEFAULT_LANG) -> str:
    """Display label for an expense category: mapped key, else the user's own name.

    A user's own category is stored as free text and is never translated — it is shown as they
    typed it, in whatever language that was.
    """
    if category is None:
        return ""
    if category.key:
        mapped = CATEGORY_LABELS[normalise_lang(lang)].get(category.key)
        return mapped or category.key.replace("_", " ").capitalize()
    return category.name or ""


def _payment_method_label(method, lang: str = DEFAULT_LANG) -> str:
    if not method:
        return ""
    value = method.value if hasattr(method, "value") else str(method)
    mapped = PAYMENT_METHOD_LABELS[normalise_lang(lang)].get(value)
    return mapped or value.replace("_", " ").capitalize()

# --- PDF fonts -------------------------------------------------------------
# The reports carry user data — addresses, owner and supplier names, notes — which in this
# product is usually Hebrew. fpdf2's built-in Helvetica is Latin-1 only and *raises*
# FPDFUnicodeEncodingException on the first Hebrew character, so the PDF endpoints used to
# return 500 for most owners.
#
# Two families, because neither covers the other's script: Noto Sans carries the Latin chrome
# and digits (and no Hebrew at all), Noto Sans Hebrew carries the Hebrew (147 glyphs, no Latin,
# not even numerals). Noto Sans is the primary; Hebrew is registered as a fallback so fpdf2
# switches per character.
FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT = "NotoSans"
FONT_FALLBACK = "NotoSansHebrew"


def _fmt(amount: Decimal, lang: str = DEFAULT_LANG) -> str:
    return f"{_t(lang, 'currency')}{amount:,.0f}"


# Table shading. The faint grid is what makes a property block read as one unit; the strong
# lines separate one property from the next. Shared with the on-screen previews, which mirror
# this layout — see `reportTheme.ts` in the web app.
GRID_LIGHT = (219, 222, 228)
GRID_STRONG = (120, 126, 138)
NET_ROW_FILL = (238, 240, 244)
TOTAL_COL_FILL = (242, 243, 246)
TOTAL_COL_FILL_NET = (223, 227, 235)


def _sign_colour(value, strong: bool = False) -> tuple[int, int, int]:
    """Green for a positive figure, red for a negative one."""
    if value >= 0:
        return (0, 100, 0) if strong else (0, 128, 0)
    return (180, 0, 0) if strong else (200, 0, 0)


def _chunks(items: list, size: int) -> list[list]:
    """Split a column list into page-width-sized blocks."""
    return [items[i:i + size] for i in range(0, len(items), size)] or [[]]


def _visual(text):
    """Logical → visual order for RTL text. A no-op for Latin-only strings."""
    return get_display(text) if isinstance(text, str) and text else text


def _bidi_args(args: tuple) -> list:
    """fpdf2's cell/multi_cell take text as the 3rd positional arg (w, h, text, ...)."""
    args = list(args)
    if len(args) >= 3:
        args[2] = _visual(args[2])
    return args


def _bidi_kwargs(kwargs: dict) -> dict:
    if "text" in kwargs:
        kwargs = {**kwargs, "text": _visual(kwargs["text"])}
    return kwargs


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------

def get_income_expense_data(db: Session, owner_id: str, year: int) -> IncomeExpenseReportResponse:
    from app.models.transaction import TransactionTypeEnum

    # Revenue belongs to the month it is *for*, expenses to the date they were paid. Filtering
    # in SQL rather than in Python: this used to load the owner's entire transaction history on
    # every run and throw away all but one year of it.
    revenue_date = func.coalesce(Transaction.month_for, Transaction.date_of_payment)
    stmt = (
        select(Transaction)
        .where(
            Transaction.owner_id == owner_id,
            or_(
                and_(
                    Transaction.type == TransactionTypeEnum.REVENUE,
                    extract("year", revenue_date) == year,
                ),
                and_(
                    Transaction.type != TransactionTypeEnum.REVENUE,
                    extract("year", Transaction.date_of_payment) == year,
                ),
            ),
        )
        .options(selectinload(Transaction.property))
    )
    filtered = list(db.scalars(stmt).all())

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


def get_expense_log_data(
    db: Session, owner_id: str, year: int, lang: str = DEFAULT_LANG
) -> ExpenseLogReportResponse:
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
            selectinload(Transaction.categories),
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
    has_multi_category = False

    for t in transactions:
        prop_owner = ""
        prop_addr = t.property_address or ""
        if t.property:
            prop_owner = t.property.property_owner or ""
            prop_addr = f"{t.property.address}, {t.property.city}" if t.property.city else t.property.address

        # An expense can carry several categories. The pivot credits the whole amount to the
        # primary one (the first the user picked, which the service mirrors into `category_id`)
        # — splitting it would invent a ratio nobody entered. The transaction list below shows
        # every tag, so a secondary category is never hidden, only uncounted in the pivot.
        tags = list(t.categories) if t.categories else ([t.category] if t.category else [])
        if len(tags) > 1:
            has_multi_category = True
        primary = t.category or (tags[0] if tags else None)
        cat_name = _category_label(primary, lang) or UNCATEGORISED_BY_LANG[normalise_lang(lang)]
        tag_labels = ", ".join(
            label for label in (_category_label(c, lang) for c in tags) if label
        )

        supplier_name = t.supplier.name if t.supplier else ""

        rows_out.append(ExpenseLogRow(
            date=t.date_of_payment.strftime("%Y-%m-%d"),
            property_address=prop_addr,
            property_owner=prop_owner,
            category_name=tag_labels,
            supplier_name=supplier_name or "",
            payment_method=_payment_method_label(t.payment_method, lang),
            amount=Decimal(str(t.amount)),
            notes=t.notes or "",
        ))

        pivot[prop_owner][prop_addr][cat_name] += Decimal(str(t.amount))

        if prop_owner not in owner_order:
            owner_order.append(prop_owner)
        if prop_addr not in prop_order[prop_owner]:
            prop_order[prop_owner].append(prop_addr)
        if cat_name not in all_categories:
            all_categories.append(cat_name)

    # Keep the catch-all at the end rather than wherever it first appeared.
    uncategorised = UNCATEGORISED_BY_LANG[normalise_lang(lang)]
    if uncategorised in all_categories:
        all_categories.remove(uncategorised)
        all_categories.append(uncategorised)

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
        has_multi_category=has_multi_category,
    )


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

class _PDF(FPDF):
    def __init__(self, title_key: str, year: int, lang: str = DEFAULT_LANG, **kwargs):
        super().__init__(**kwargs)
        self.lang = normalise_lang(lang)
        self.rtl = self.lang == "he"
        self._title = _t(self.lang, title_key)
        self._year = year
        for style, suffix in (("", "Regular"), ("B", "Bold")):
            self.add_font(FONT, style, FONTS_DIR / f"{FONT}-{suffix}.ttf")
            self.add_font(FONT_FALLBACK, style, FONTS_DIR / f"{FONT_FALLBACK}-{suffix}.ttf")
        self.set_fallback_fonts([FONT_FALLBACK])

    def set_font(self, family=None, style="", size=0):
        super().set_font(family, style, size)
        # Remember what the caller actually asked for — see _restore_font.
        self._font_intent = (self.font_family, self.font_style, self.font_size_pt)

    def _restore_font(self) -> None:
        """Undo the font switch that drawing Hebrew leaves behind.

        Rendering a Hebrew string swaps `current_font` to the fallback family and leaves it
        there. A following `set_font` with the same family/style/size is then a no-op — fpdf2
        compares against its own record, which the fallback never updated — so every later
        cell keeps drawing through Noto Sans Hebrew. That font has no digits and no Latin, so
        the text is silently dropped: in the expense-log pivot, one Hebrew category name in the
        first column blanked every amount in the row.
        """
        intent = getattr(self, "_font_intent", None)
        if intent is None or self.current_font is None:
            return
        family, style, size = intent
        # Exact match, not a prefix: "NotoSansHebrew" starts with "NotoSans".
        if self.current_font.name.lower() == family.lower():
            return
        self.font_family = ""  # defeat set_font's no-op guard
        super().set_font(family, style, size)

    def cell(self, *args, **kwargs):
        """Reorder RTL text before it is drawn.

        A Unicode font gets the glyphs right but draws them in logical order, so Hebrew comes
        out reversed. `get_display` applies the Unicode bidi algorithm to produce visual order
        (Hebrew needs reordering only — no glyph reshaping, unlike Arabic).

        This is overridden here rather than applied at each call site because user data reaches
        a cell in ~10 places across both generators, and a missed one is a 500 in production.
        Latin-only strings pass through `get_display` unchanged, so applying it everywhere is
        safe — including for columns added later.
        """
        self._restore_font()
        return super().cell(*_bidi_args(args), **_bidi_kwargs(kwargs))

    def multi_cell(self, *args, **kwargs):
        self._restore_font()
        return super().multi_cell(*_bidi_args(args), **_bidi_kwargs(kwargs))

    def fit(self, text: str, width: float) -> str:
        """Truncate `text` to what actually fits a `width` mm cell at the current font.

        The call sites used to slice by character count (`address[:22]` into a 22 mm cell),
        which is a guess in the wrong unit: 22 characters of Latin at 7 pt is roughly 30 mm, and
        Hebrew glyphs measure differently again — so text spilled past its cell border either
        way. Measuring is exact.

        Must be called *after* `set_font` (width depends on the active font and size) and
        *before* the text reaches `cell`, which applies the bidi reordering. Measuring the
        logical string is correct: reordering does not change total width.
        """
        if not text:
            return text
        available = width - 2 * self.c_margin
        if self.get_string_width(text) <= available:
            return text
        while text and self.get_string_width(text + "…") > available:
            text = text[:-1]
        return f"{text}…" if text else text

    def columns_per_block(self, fixed_w: float, col_w: float) -> int:
        """How many `col_w` columns fit across the page beside `fixed_w` of fixed columns.

        Never returns 0: a single column that is too wide still gets drawn (and its contents
        fitted), which is preferable to emitting an empty table.
        """
        return max(1, int((self.epw - fixed_w) // col_w))

    def ensure_room(self, height: float) -> None:
        """Start a new page unless `height` mm of table still fits below the cursor.

        Auto page break handles the data rows, but would happily strand a header row alone at
        the bottom of a page with its table overleaf.
        """
        if self.get_y() + height > self.page_break_trigger:
            self.add_page()

    # --- Right-to-left tables ---------------------------------------------
    #
    # fpdf2 draws cells left to right and has no notion of table direction, so a Hebrew report
    # would otherwise read backwards: the property column on the left, January on the left,
    # the total on the right. Callers therefore describe a row in *logical* order — leading
    # column first — and these helpers place it, reversing the draw order for Hebrew so the
    # leading column lands against the right margin.

    def col_x(self, widths: list[float], index: int) -> float:
        """Left edge of logical column `index` given every column width in the row."""
        if self.rtl:
            return self.l_margin + sum(widths[index + 1:])
        return self.l_margin + sum(widths[:index])

    def row(self, cells: list[tuple], height: float, x: float | None = None) -> None:
        """Draw one row from a list of `(width, text, kwargs)` in logical order.

        Cells with no explicit alignment are aligned to the reading edge, so labels sit
        against the right margin in Hebrew and the left margin in English. Numbers keep the
        right alignment their callers ask for in both directions.
        """
        self.set_x(self.l_margin if x is None else x)
        for width, text, options in (reversed(cells) if self.rtl else cells):
            options = dict(options)
            if self.rtl and "align" not in options:
                options["align"] = "R"
            self.cell(width, height, text, **options)

    def header(self):
        self.set_font(FONT, "B", 12)
        self.cell(0, 8, f"{self._title} - {self._year}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font(FONT, "", 8)
        self.cell(0, 6, f"{_t(self.lang, 'page')} {self.page_no()}", align="C")


def generate_income_expense_pdf(
    data: IncomeExpenseReportResponse, lang: str = DEFAULT_LANG
) -> bytes:
    """One block per property — Revenue, Expenses and Net on adjacent rows, months as columns.

    Properties used to be column groups, which put the axis that grows without limit on the
    axis capped by the page width: past a handful of properties the table had to be sliced
    into blocks. Months are a fixed twelve, so as columns they can never overflow.

    Keeping a property's three figures together (rather than in separate Revenue and Expenses
    bands) is what makes the report readable: you can compare a month's income against its
    costs without looking in two places, and the Total column gives that property's net.
    """
    pdf = _PDF("income_title", data.year, lang=lang, orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def t(key: str) -> str:
        return _t(pdf.lang, key)

    ROW_H = 4.6
    HEADER_H = 6
    BLOCK_H = ROW_H * 3
    metric_labels = (t("revenue"), t("expenses"), t("net"))

    # Size the figure columns to the widest number this report actually holds, then give the
    # property column whatever is left.
    pdf.set_font(FONT, "", 7)
    monthly = [
        f"{value:,.0f}"
        for owner in data.owners
        for prop in owner.properties
        for cell in prop.months.values()
        for value in (cell.revenue, cell.expenses, cell.net)
    ]
    # The owner-total row adds up every property, so it is wider than anything above it.
    monthly += [
        f"{sum(p.months.get(m, MonthCell()).net for p in owner.properties):,.0f}"
        for owner in data.owners
        for m in range(1, 13)
    ]
    totals = [
        f"{value:,.0f}"
        for owner in data.owners
        for prop in owner.properties
        for value in (prop.total.revenue, prop.total.expenses, prop.total.net)
    ] + [f"{owner.total.net:,.0f}" for owner in data.owners]

    pad = 2 * pdf.c_margin + 0.4
    months_abbr = MONTH_ABBR[pdf.lang]
    # Measure in bold throughout: the header row, the Net row and every total are drawn bold,
    # which is wider than the regular weight — sizing columns from the regular measurement is
    # what clipped the figures before.
    pdf.set_font(FONT, "B", 7)
    widest_month = max(max((pdf.get_string_width(v) for v in monthly), default=0),
                       *(pdf.get_string_width(m) for m in months_abbr))
    widest_total = max(max((pdf.get_string_width(v) for v in totals), default=0),
                       pdf.get_string_width(t("total")))
    METRIC_W = max(pdf.get_string_width(m) for m in metric_labels) + pad + 1
    pdf.set_font(FONT, "", 7)

    MONTH_W = widest_month + pad
    TOT_W = widest_total + pad
    PROP_W = pdf.epw - METRIC_W - 12 * MONTH_W - TOT_W
    if PROP_W < 40:
        # Extreme figures: keep the addresses legible and let `fit` trim any outlier number.
        PROP_W = 40
        MONTH_W = (pdf.epw - PROP_W - METRIC_W - TOT_W) / 12

    widths = [PROP_W, METRIC_W] + [MONTH_W] * 12 + [TOT_W]
    # Where the metric rows start, i.e. everything except the property column.
    metrics_x = pdf.l_margin if pdf.rtl else pdf.l_margin + PROP_W

    def draw_column_header():
        pdf.set_font(FONT, "B", 7)
        pdf.set_fill_color(31, 45, 74)
        pdf.set_text_color(255, 255, 255)
        pdf.row(
            [(PROP_W, t("property"), {"border": 1, "fill": True}),
             (METRIC_W, "", {"border": 1, "fill": True})]
            + [(MONTH_W, name, {"border": 1, "fill": True, "align": "R"}) for name in months_abbr]
            + [(TOT_W, t("total"), {"border": 1, "fill": True, "align": "R"})],
            HEADER_H,
        )
        pdf.set_text_color(0, 0, 0)
        pdf.ln()

    def new_page_keeps_header(height: float):
        """Break the page ourselves so the column header is redrawn on the next one."""
        if pdf.get_y() + height > pdf.page_break_trigger:
            pdf.add_page()
            draw_column_header()

    def band(text: str, rgb: tuple[int, int, int], height: float = 7, size: int = 9):
        pdf.set_font(FONT, "B", size)
        pdf.set_fill_color(*rgb)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, height, text, border=1, fill=True, align="R" if pdf.rtl else "L",
                 new_x="LMARGIN", new_y="NEXT")

    def draw_property_block(prop):
        """The address spans the three metric rows beside it.

        The grid *inside* a block is deliberately faint and the outline around it is not, so
        the eye groups a property's revenue, expenses and net as one unit and the strongest
        lines on the page are the ones separating one property from the next.
        """
        new_page_keeps_header(BLOCK_H)
        y = pdf.get_y()
        default_line_width = pdf.line_width

        pdf.set_draw_color(*GRID_LIGHT)
        pdf.set_line_width(0.1)

        pdf.set_font(FONT, "", 7)
        pdf.set_xy(pdf.col_x(widths, 0), y)
        pdf.cell(PROP_W, BLOCK_H, pdf.fit(prop.property_address, PROP_W), border=0,
                 align="R" if pdf.rtl else "L")

        months = [prop.months.get(m, MonthCell()) for m in range(1, 13)]
        rows = (
            (metric_labels[0], [c.revenue for c in months], prop.total.revenue, (0, 128, 0), False),
            (metric_labels[1], [c.expenses for c in months], prop.total.expenses, (200, 0, 0), False),
            (metric_labels[2], [c.net for c in months], prop.total.net, None, True),
        )
        for index, (label, values, total, colour, is_net) in enumerate(rows):
            pdf.set_y(y + index * ROW_H)
            pdf.set_font(FONT, "B" if is_net else "", 7)
            row_fill = NET_ROW_FILL if is_net else None
            cells = [(METRIC_W, f" {label}",
                      {"border": 1, "fill": is_net, "_fill_rgb": row_fill})]
            for value in values:
                # An empty month reads as a dash, so the real figures stand out.
                cells.append((MONTH_W, f"{value:,.0f}" if value else "—",
                              {"border": 1, "fill": is_net, "align": "R",
                               "_fill_rgb": row_fill,
                               "_colour": colour or _sign_colour(value)}))
            # The Total column is tinted and bold in every row — it is the number most people
            # open the report for.
            cells.append((TOT_W, f"{total:,.0f}",
                          {"border": 1, "fill": True, "align": "R", "_bold": True,
                           "_fill_rgb": TOTAL_COL_FILL_NET if is_net else TOTAL_COL_FILL,
                           "_colour": colour or _sign_colour(total)}))
            _draw_coloured_row(pdf, cells, ROW_H, metrics_x, is_net)

        # The block outline and the rules flanking the address and Total columns, all in the
        # stronger colour so they stand out against the faint grid drawn above.
        pdf.set_draw_color(*GRID_STRONG)
        pdf.set_line_width(0.3)
        pdf.rect(pdf.l_margin, y, sum(widths), BLOCK_H)
        for sep_x in (
            pdf.col_x(widths, 0) + (0 if pdf.rtl else PROP_W),
            pdf.col_x(widths, len(widths) - 1) + (TOT_W if pdf.rtl else 0),
        ):
            pdf.line(sep_x, y, sep_x, y + BLOCK_H)

        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(default_line_width)
        pdf.set_xy(pdf.l_margin, y + BLOCK_H)

    for owner in data.owners:
        owner_label = owner.owner_name if owner.owner_name else t("no_owner")
        new_page_keeps_header(HEADER_H * 2 + BLOCK_H)
        band(f'{t("owner")}: {owner_label}', (220, 230, 245))
        draw_column_header()

        for prop in owner.properties:
            draw_property_block(prop)

        # Net per month across the owner's properties, then the owner's net for the year.
        new_page_keeps_header(HEADER_H)
        pdf.set_font(FONT, "B", 7)
        pdf.set_fill_color(230, 240, 230)
        cells = [(PROP_W + METRIC_W, f' {t("owner_total_net")}', {"border": 1, "fill": True})]
        for m in range(1, 13):
            net = sum(p.months.get(m, MonthCell()).net for p in owner.properties)
            cells.append((MONTH_W, f"{net:,.0f}" if net else "—",
                          {"border": 1, "fill": True, "align": "R", "_colour": _sign_colour(net)}))
        cells.append((TOT_W, f"{owner.total.net:,.0f}",
                      {"border": 1, "fill": True, "align": "R",
                       "_colour": _sign_colour(owner.total.net)}))
        _draw_coloured_row(pdf, cells, HEADER_H, None, False)
        pdf.ln(HEADER_H + 4)

    # Grand total summary. Widths come from the text so a large portfolio's totals are not
    # clipped by a hand-picked cell width.
    new_page_keeps_header(7)
    pdf.set_font(FONT, "B", 9)
    pdf.set_fill_color(200, 220, 200)
    summary = [
        (t("grand_total"), None),
        (f'{t("revenue")}: {_fmt(data.grand_total.revenue, pdf.lang)}', None),
        (f'{t("expenses")}: {_fmt(data.grand_total.expenses, pdf.lang)}', None),
        (f'{t("net")}: {_fmt(data.grand_total.net, pdf.lang)}',
         _sign_colour(data.grand_total.net, strong=True)),
    ]
    cells = [(pdf.get_string_width(text) + 6, text,
              {"border": 1, "fill": True, "_colour": colour})
             for text, colour in summary]
    _draw_coloured_row(pdf, cells, 7, None, False)

    return bytes(pdf.output())


def _draw_coloured_row(pdf: "_PDF", cells: list[tuple], height: float,
                       x: float | None, bold_all: bool) -> None:
    """`_PDF.row` with per-cell text colour and an optional bold override.

    Colour is carried in the cell options as `_colour` / `_bold` rather than being applied by
    the caller, because in a right-to-left report the cells are drawn in reverse order — so
    setting the colour before the loop would attach it to the wrong cell.
    """
    ordered = list(reversed(cells)) if pdf.rtl else cells
    pdf.set_x(pdf.l_margin if x is None else x)
    for width, text, options in ordered:
        options = dict(options)
        colour = options.pop("_colour", None)
        bold = options.pop("_bold", False)
        fill_rgb = options.pop("_fill_rgb", None)
        if pdf.rtl and "align" not in options:
            options["align"] = "R"
        if bold or bold_all:
            pdf.set_font(FONT, "B", pdf.font_size_pt)
        if colour:
            pdf.set_text_color(*colour)
        if fill_rgb:
            pdf.set_fill_color(*fill_rgb)
        pdf.cell(width, height, text, **options)
        pdf.set_text_color(0, 0, 0)


def generate_expense_log_pdf(data: ExpenseLogReportResponse, lang: str = DEFAULT_LANG) -> bytes:
    pdf = _PDF("expense_title", data.year, lang=lang, orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def t(key: str) -> str:
        return _t(pdf.lang, key)

    # Part 1: transaction list
    headers = [t("date"), t("property"), t("category"), t("supplier"),
               t("method"), t("amount"), t("notes")]
    widths = [22, 62, 38, 40, 26, 22, 66]

    def draw_list_header():
        pdf.set_font(FONT, "B", 9)
        pdf.set_fill_color(220, 220, 220)
        pdf.row([(w, h, {"border": 1, "fill": True}) for h, w in zip(headers, widths)], 6)
        pdf.ln()

    draw_list_header()
    pdf.set_font(FONT, "", 7)
    for row in data.rows:
        # Break the page ourselves so the column header is repeated, rather than letting rows
        # spill onto a fresh page under no header at all.
        if pdf.get_y() + 5 > pdf.page_break_trigger:
            pdf.add_page()
            draw_list_header()
            pdf.set_font(FONT, "", 7)
        values = [
            row.date,
            row.property_address,
            row.category_name,
            row.supplier_name,
            row.payment_method,
            f"{row.amount:,.0f}",
            row.notes,
        ]
        cells = []
        for index, (value, w) in enumerate(zip(values, widths)):
            options = {"border": 1}
            if index == 5:  # the amount column reads right-aligned either way
                options["align"] = "R"
            cells.append((w, pdf.fit(value, w), options))
        pdf.row(cells, 5)
        pdf.ln()

    pdf.ln(6)

    # Part 2: pivot summary — one row per property, one column per category.
    #
    # This used to be the other way round, which put the unbounded axis (properties grow with
    # the portfolio) on the page-width axis and forced the table into blocks. Categories are
    # bounded — the twelve built in plus the user's own, and only those actually used that
    # year — so as columns they normally fit, and a property is now just another row.
    if data.owners:
        # Keep the heading with at least the start of its table rather than stranding it at
        # the foot of the page.
        pdf.ensure_room(7 + 5 + 2 + 6 + 5 * 3)
        pdf.set_font(FONT, "B", 10)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 7, t("summary_title"), align="R" if pdf.rtl else "L",
                 new_x="LMARGIN", new_y="NEXT")
        if data.has_multi_category:
            pdf.set_font(FONT, "", 7)
            pdf.set_x(pdf.l_margin)
            pdf.cell(0, 5, t("multi_note"), align="R" if pdf.rtl else "L",
                     new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        ROW_H = 5
        pdf.set_font(FONT, "", 7)
        amounts = [
            f"{amount:,.0f}"
            for owner in data.owners
            for prop in owner.properties
            for amount in prop.categories.values()
        ]
        pad = 2 * pdf.c_margin + 0.4
        widest_amount = max(
            max((pdf.get_string_width(a) for a in amounts), default=0),
            pdf.get_string_width("999,999"),
        )
        # Headers are drawn bold, which is wider than the regular weight the figures use —
        # measure them as they will actually be drawn or the labels come out truncated.
        pdf.set_font(FONT, "B", 7)
        widest_header = max((pdf.get_string_width(c) for c in data.categories), default=0)
        total_header = pdf.get_string_width(t("total"))
        pdf.set_font(FONT, "", 7)

        CAT_W = max(widest_amount, widest_header) + pad
        TOT_W = max(pdf.get_string_width(f"{data.grand_total:,.0f}"), total_header) + pad

        # Categories are usually few enough to fit; chunk as a backstop for a very wide list.
        per_block = pdf.columns_per_block(40 + TOT_W, CAT_W)
        blocks = _chunks(data.categories, per_block)

        for block_index, block in enumerate(blocks):
            # The Total column is the property's total across *all* categories, so it belongs
            # on every block rather than only the last.
            PROP_W = pdf.epw - len(block) * CAT_W - TOT_W

            def draw_column_header(block=block, PROP_W=PROP_W):
                pdf.set_font(FONT, "B", 7)
                pdf.set_fill_color(31, 45, 74)
                pdf.set_text_color(255, 255, 255)
                pdf.row(
                    [(PROP_W, t("property"), {"border": 1, "fill": True})]
                    + [(CAT_W, pdf.fit(cat, CAT_W), {"border": 1, "fill": True, "align": "R"})
                       for cat in block]
                    + [(TOT_W, t("total"), {"border": 1, "fill": True, "align": "R"})],
                    6,
                )
                pdf.set_text_color(0, 0, 0)
                pdf.ln()

            def room_for(height: float):
                if pdf.get_y() + height > pdf.page_break_trigger:
                    pdf.add_page()
                    draw_column_header()

            if block_index:
                pdf.ln(4)
            room_for(6 + ROW_H * 2)
            draw_column_header()

            def summary_row(label, amounts_by_cat, total, fill, bold=True):
                pdf.set_font(FONT, "B" if bold else "", 7)
                cells = [(PROP_W, label, {"border": 1, "fill": fill is not None})]
                for cat in block:
                    amount = amounts_by_cat.get(cat, Decimal("0"))
                    cells.append((CAT_W, f"{amount:,.0f}" if amount else "",
                                  {"border": 1, "fill": fill is not None, "align": "R"}))
                cells.append((TOT_W, f"{total:,.0f}",
                              {"border": 1, "fill": fill is not None, "align": "R"}))
                if fill is not None:
                    pdf.set_fill_color(*fill)
                pdf.row(cells, ROW_H)
                pdf.ln()

            for owner in data.owners:
                room_for(ROW_H * 2)
                pdf.set_font(FONT, "B", 7)
                pdf.set_fill_color(220, 230, 245)
                pdf.set_x(pdf.l_margin)
                pdf.cell(0, ROW_H, f'  {t("owner")}: {owner.owner_name or t("no_owner")}',
                         border=1, fill=True, align="R" if pdf.rtl else "L",
                         new_x="LMARGIN", new_y="NEXT")

                for prop in owner.properties:
                    room_for(ROW_H)
                    pdf.set_font(FONT, "", 7)
                    summary_row(pdf.fit(prop.property_address, PROP_W), prop.categories,
                                prop.total, None, bold=False)

                room_for(ROW_H)
                owner_by_cat = {
                    cat: sum((p.categories.get(cat, Decimal("0")) for p in owner.properties),
                             Decimal("0"))
                    for cat in block
                }
                summary_row(t("owner_total"), owner_by_cat, owner.total, (240, 240, 240))

            # Grand total across every owner.
            room_for(ROW_H)
            summary_row(t("total_row"), data.grand_total_by_category, data.grand_total,
                        (230, 240, 230))

    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# CSV generation
# ---------------------------------------------------------------------------

def generate_income_expense_csv(
    data: IncomeExpenseReportResponse, lang: str = DEFAULT_LANG
) -> str:
    """Long format — one row per property per month — which pivots cleanly in a spreadsheet."""
    lang = normalise_lang(lang)
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow([f"{_t(lang, 'income_title')} - {data.year}"])
    writer.writerow([])
    writer.writerow([_t(lang, "owner"), _t(lang, "property"), "Month",
                     _t(lang, "revenue"), _t(lang, "expenses"), _t(lang, "net")])

    for owner in data.owners:
        owner_name = owner.owner_name or _t(lang, "no_owner")
        for prop in owner.properties:
            for m in range(1, 13):
                cell = prop.months.get(m, MonthCell())
                if cell.revenue or cell.expenses:
                    writer.writerow([
                        owner_name,
                        prop.property_address,
                        MONTH_NAMES_BY_LANG[lang][m - 1],
                        float(cell.revenue),
                        float(cell.expenses),
                        float(cell.net),
                    ])
            writer.writerow([
                owner_name,
                prop.property_address,
                _t(lang, "total_row"),
                float(prop.total.revenue),
                float(prop.total.expenses),
                float(prop.total.net),
            ])
        writer.writerow([
            owner_name, _t(lang, "owner_total"), "",
            float(owner.total.revenue),
            float(owner.total.expenses),
            float(owner.total.net),
        ])
        writer.writerow([])

    writer.writerow([
        _t(lang, "grand_total"), "", "",
        float(data.grand_total.revenue),
        float(data.grand_total.expenses),
        float(data.grand_total.net),
    ])

    return buf.getvalue()


def generate_expense_log_csv(data: ExpenseLogReportResponse, lang: str = DEFAULT_LANG) -> str:
    lang = normalise_lang(lang)
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow([f"{_t(lang, 'expense_title')} - {data.year}"])
    writer.writerow([])

    # Part 1: transaction list
    writer.writerow([_t(lang, "date"), _t(lang, "property"), _t(lang, "owner"),
                     _t(lang, "category"), _t(lang, "supplier"), _t(lang, "method"),
                     _t(lang, "amount"), _t(lang, "notes")])
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
    writer.writerow([_t(lang, "summary_title")])
    writer.writerow([])

    # One row per property, one column per category — same orientation as the PDF.
    writer.writerow([_t(lang, "owner"), _t(lang, "property")]
                    + list(data.categories) + [_t(lang, "total")])

    for owner in data.owners:
        owner_name = owner.owner_name or _t(lang, "no_owner")
        for prop in owner.properties:
            writer.writerow(
                [owner_name, prop.property_address]
                + [float(prop.categories.get(cat, Decimal("0"))) for cat in data.categories]
                + [float(prop.total)]
            )
        writer.writerow(
            [owner_name, _t(lang, "owner_total")]
            + [
                float(sum(
                    (p.categories.get(cat, Decimal("0")) for p in owner.properties),
                    Decimal("0"),
                ))
                for cat in data.categories
            ]
            + [float(owner.total)]
        )

    writer.writerow(
        [_t(lang, "total_row"), ""]
        + [float(data.grand_total_by_category.get(cat, Decimal("0"))) for cat in data.categories]
        + [float(data.grand_total)]
    )

    return buf.getvalue()

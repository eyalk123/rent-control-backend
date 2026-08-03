"""Full-stack tests for /reports.

These assert the report endpoints generate successfully, record a ReportExport,
and stay owner-scoped. Exact PDF bytes / CSV layout are intentionally not pinned.
"""
import csv
import io
from datetime import date
from decimal import Decimal

from tests.conftest import OWNER_A, OWNER_B
from tests.factories import (
    make_expense_category,
    make_property,
    make_supplier,
    make_transaction,
)
from app.models.transaction import PaymentMethodEnum, TransactionTypeEnum
from app.services.report_service import (
    UNCATEGORISED,
    get_expense_log_data,
    get_income_expense_data,
)


def _seed_year(db_session, owner_id=OWNER_A):
    prop = make_property(db_session, owner_id=owner_id, property_owner="Alice")
    cat = make_expense_category(db_session, owner_id=owner_id, name="Repairs")
    make_transaction(
        db_session,
        owner_id=owner_id,
        type=TransactionTypeEnum.REVENUE,
        property_id=prop.id,
        amount=8000,
        date_of_payment=date(2025, 3, 1),
        month_for=date(2025, 3, 1),
    )
    make_transaction(
        db_session,
        owner_id=owner_id,
        type=TransactionTypeEnum.EXPENSE,
        property_id=prop.id,
        amount=1500,
        date_of_payment=date(2025, 4, 1),
        categories=[cat],
        payment_method=None,
    )
    return prop


def _seed_hebrew_year(db_session, owner_id=OWNER_A):
    """Every field that reaches a PDF cell, in Hebrew.

    The product is Hebrew-first, so this is the ordinary case, not an edge case: address,
    property owner, expense category, supplier and the transaction note.
    """
    prop = make_property(
        db_session,
        owner_id=owner_id,
        address="רחוב הרצל 12",
        city="תל אביב",
        property_owner="דנה לוי",
    )
    cat = make_expense_category(db_session, owner_id=owner_id, name="תיקונים")
    supplier = make_supplier(db_session, owner_id=owner_id, name="אקמה שרברבות", categories=[cat])
    make_transaction(
        db_session,
        owner_id=owner_id,
        type=TransactionTypeEnum.REVENUE,
        property_id=prop.id,
        amount=8000,
        date_of_payment=date(2025, 3, 1),
        month_for=date(2025, 3, 1),
    )
    make_transaction(
        db_session,
        owner_id=owner_id,
        type=TransactionTypeEnum.EXPENSE,
        property_id=prop.id,
        amount=1500,
        date_of_payment=date(2025, 4, 1),
        categories=[cat],
        supplier_id=supplier.id,
        payment_method=None,
        notes="תיקון נזילה במטבח",
    )
    return prop


def test_income_expense_pdf_with_hebrew_data(client, db_session):
    """Regression: Hebrew text used to raise FPDFUnicodeEncodingException → HTTP 500."""
    _seed_hebrew_year(db_session)
    resp = client.get("/reports/income-expense", params={"year": 2025})
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_expense_log_pdf_with_hebrew_data(client, db_session):
    """Same regression on the other generator — this one also renders supplier and notes."""
    _seed_hebrew_year(db_session)
    resp = client.get("/reports/expense-log", params={"year": 2025})
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def _seed_wide_portfolio(db_session, owner_id=OWNER_A, count=15):
    """More properties than fit across one landscape page, with six-digit figures.

    Past ~7 columns the tables used to run off the right edge of the page; the columns are now
    split into blocks instead. Long addresses and large amounts also exercise the cell fitting.
    """
    for i in range(count):
        prop = make_property(
            db_session,
            owner_id=owner_id,
            address=f"{i + 1} Rothschild Boulevard, Apartment {i + 1}",
            property_owner="Alice",
        )
        cat = make_expense_category(db_session, owner_id=owner_id, name=f"Category {i + 1}")
        make_transaction(
            db_session,
            owner_id=owner_id,
            type=TransactionTypeEnum.REVENUE,
            property_id=prop.id,
            amount=999_999,
            date_of_payment=date(2025, 3, 1),
            month_for=date(2025, 3, 1),
        )
        make_transaction(
            db_session,
            owner_id=owner_id,
            type=TransactionTypeEnum.EXPENSE,
            property_id=prop.id,
            amount=123_456,
            date_of_payment=date(2025, 4, 1),
            categories=[cat],
            payment_method=None,
        )


class TestExpenseLogFigures:
    """What the pivot claims has to match what the totals say."""

    def test_uncategorised_expenses_get_their_own_column(self, client, db_session):
        """Regression: they counted towards every total but had no column, so the visible
        columns silently added up to less than the total printed beside them."""
        prop = make_property(db_session, owner_id=OWNER_A, property_owner="Alice")
        cat = make_expense_category(db_session, owner_id=OWNER_A, name="Repairs")
        make_transaction(
            db_session, owner_id=OWNER_A, type=TransactionTypeEnum.EXPENSE,
            property_id=prop.id, amount=100, date_of_payment=date(2025, 4, 1), categories=[cat],
        )
        make_transaction(  # no category at all
            db_session, owner_id=OWNER_A, type=TransactionTypeEnum.EXPENSE,
            property_id=prop.id, amount=900, date_of_payment=date(2025, 5, 1),
        )

        data = get_expense_log_data(db_session, OWNER_A, 2025)
        p = data.owners[0].properties[0]

        assert UNCATEGORISED in data.categories
        assert data.categories[-1] == UNCATEGORISED  # and it sorts last
        columns_sum = sum(p.categories.get(c, Decimal("0")) for c in data.categories)
        assert columns_sum == p.total == Decimal("1000")

    def test_columns_reconcile_to_the_grand_total(self, client, db_session):
        prop = make_property(db_session, owner_id=OWNER_A, property_owner="Alice")
        make_transaction(
            db_session, owner_id=OWNER_A, type=TransactionTypeEnum.EXPENSE,
            property_id=prop.id, amount=250, date_of_payment=date(2025, 6, 1),
        )

        data = get_expense_log_data(db_session, OWNER_A, 2025)

        total = sum(
            prop_data.categories.get(cat, Decimal("0"))
            for owner in data.owners
            for prop_data in owner.properties
            for cat in data.categories
        )
        assert total == data.grand_total

    def test_multi_category_expense_counts_once_and_shows_every_tag(self, client, db_session):
        """Agreed rule: the pivot credits the primary category, the list shows them all."""
        prop = make_property(db_session, owner_id=OWNER_A, property_owner="Alice")
        repairs = make_expense_category(db_session, owner_id=OWNER_A, name="Repairs")
        plumbing = make_expense_category(db_session, owner_id=OWNER_A, name="Plumbing")
        make_transaction(
            db_session, owner_id=OWNER_A, type=TransactionTypeEnum.EXPENSE,
            property_id=prop.id, amount=1000, date_of_payment=date(2025, 4, 1),
            categories=[repairs, plumbing],
        )

        data = get_expense_log_data(db_session, OWNER_A, 2025)
        p = data.owners[0].properties[0]

        assert p.categories == {"Repairs": Decimal("1000")}  # counted once, under the primary
        assert p.total == Decimal("1000")
        assert data.rows[0].category_name == "Repairs, Plumbing"  # but both are visible
        assert data.has_multi_category is True

    def test_built_in_categories_print_their_label_not_their_key(self, client, db_session):
        """The apps translate `property_tax` to "Property tax"; the report printed the key."""
        prop = make_property(db_session, owner_id=OWNER_A, property_owner="Alice")
        builtin = make_expense_category(db_session, owner_id=None, key="property_tax", name=None)
        make_transaction(
            db_session, owner_id=OWNER_A, type=TransactionTypeEnum.EXPENSE,
            property_id=prop.id, amount=500, date_of_payment=date(2025, 4, 1),
            categories=[builtin], payment_method=PaymentMethodEnum.BANK_TRANSFER,
        )

        data = get_expense_log_data(db_session, OWNER_A, 2025)

        assert "Property tax" in data.categories
        assert "property_tax" not in data.categories
        assert data.rows[0].payment_method == "Bank transfer"  # not the raw enum


class TestHebrewExport:
    """`?lang=he` renders the report itself in Hebrew, not just the data it contains."""

    def test_csv_headers_are_translated(self, client, db_session):
        _seed_hebrew_year(db_session)

        body = client.get(
            "/reports/expense-log", params={"year": 2025, "format": "csv", "lang": "he"}
        ).content.decode("utf-8-sig")

        assert "יומן הוצאות" in body
        assert "תאריך" in body and "נכס" in body and "אמצעי תשלום" in body
        assert "Date" not in body and "Property" not in body

    def test_categories_and_payment_methods_are_translated(self, client, db_session):
        prop = make_property(db_session, owner_id=OWNER_A, property_owner="דנה לוי")
        builtin = make_expense_category(db_session, owner_id=None, key="property_tax", name=None)
        make_transaction(
            db_session, owner_id=OWNER_A, type=TransactionTypeEnum.EXPENSE,
            property_id=prop.id, amount=500, date_of_payment=date(2025, 4, 1),
            categories=[builtin], payment_method=PaymentMethodEnum.BANK_TRANSFER,
        )

        data = get_expense_log_data(db_session, OWNER_A, 2025, "he")

        assert "ארנונה" in data.categories          # not "Property tax", not "property_tax"
        assert data.rows[0].payment_method == "העברה בנקאית"

    def test_uncategorised_column_is_translated(self, client, db_session):
        prop = make_property(db_session, owner_id=OWNER_A, property_owner="דנה לוי")
        make_transaction(
            db_session, owner_id=OWNER_A, type=TransactionTypeEnum.EXPENSE,
            property_id=prop.id, amount=900, date_of_payment=date(2025, 5, 1),
        )

        data = get_expense_log_data(db_session, OWNER_A, 2025, "he")

        assert data.categories == ["(ללא קטגוריה)"]

    def test_both_pdf_endpoints_render_in_hebrew(self, client, db_session):
        _seed_hebrew_year(db_session)

        for path in ("/reports/income-expense", "/reports/expense-log"):
            resp = client.get(path, params={"year": 2025, "lang": "he"})
            assert resp.status_code == 200, path
            assert resp.content[:4] == b"%PDF"

    def test_an_unsupported_language_is_rejected(self, client, db_session):
        _seed_year(db_session)
        resp = client.get("/reports/income-expense", params={"year": 2025, "lang": "fr"})
        assert resp.status_code == 422

    def test_english_stays_the_default(self, client, db_session):
        _seed_year(db_session)

        body = client.get(
            "/reports/income-expense", params={"year": 2025, "format": "csv"}
        ).content.decode("utf-8-sig")

        assert "Income & Expense Report" in body


def test_expense_log_csv_pivot_is_property_per_row_and_reconciles(client, db_session):
    """The CSV pivot follows the PDF: a property is a row, a category is a column."""
    prop = make_property(db_session, owner_id=OWNER_A, property_owner="Alice")
    cat = make_expense_category(db_session, owner_id=OWNER_A, name="Repairs")
    make_transaction(
        db_session, owner_id=OWNER_A, type=TransactionTypeEnum.EXPENSE,
        property_id=prop.id, amount=100, date_of_payment=date(2025, 4, 1), categories=[cat],
    )
    make_transaction(
        db_session, owner_id=OWNER_A, type=TransactionTypeEnum.EXPENSE,
        property_id=prop.id, amount=900, date_of_payment=date(2025, 5, 1),
    )

    body = client.get(
        "/reports/expense-log", params={"year": 2025, "format": "csv"}
    ).content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(body)))

    header = next(r for r in rows if r[:2] == ["Owner", "Property"])
    assert header[0] == "Owner" and header[-1] == "Total"
    assert UNCATEGORISED in header

    data_row = rows[rows.index(header) + 1]
    figures = [float(v) for v in data_row[2:-1]]
    assert sum(figures) == float(data_row[-1]) == 1000.0


def test_income_report_uses_the_month_the_rent_is_for(client, db_session):
    """Rent paid on 31 December for January belongs to January's year."""
    prop = make_property(db_session, owner_id=OWNER_A, property_owner="Alice")
    make_transaction(
        db_session, owner_id=OWNER_A, type=TransactionTypeEnum.REVENUE,
        property_id=prop.id, amount=5000,
        date_of_payment=date(2024, 12, 31), month_for=date(2025, 1, 1),
    )

    data_2025 = get_income_expense_data(db_session, OWNER_A, 2025)
    data_2024 = get_income_expense_data(db_session, OWNER_A, 2024)

    assert data_2025.grand_total.revenue == Decimal("5000")
    assert data_2025.owners[0].properties[0].months[1].revenue == Decimal("5000")
    assert data_2024.grand_total.revenue == Decimal("0")


def _track_right_edge(monkeypatch) -> list[float]:
    """Record how far past the right margin each drawn cell ends (negative = inside).

    Asserting on the returned numbers is what makes the wide-portfolio tests real: the old
    code produced a perfectly valid PDF, it just drew the columns off the edge of the page.
    """
    from app.services import report_service

    overflow: list[float] = []
    original = report_service._PDF.cell

    def cell(self, *args, **kwargs):
        w = args[0] if args else kwargs.get("w", 0)
        if w:  # w=0 means "stretch to the right margin", which cannot overflow
            overflow.append(self.get_x() + w - (self.w - self.r_margin))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(report_service._PDF, "cell", cell)
    return overflow


def test_income_expense_pdf_with_a_wide_portfolio(client, db_session, monkeypatch):
    """Regression: 15 properties ran off the page instead of wrapping into new blocks."""
    _seed_wide_portfolio(db_session)
    overflow = _track_right_edge(monkeypatch)

    resp = client.get("/reports/income-expense", params={"year": 2025})

    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"
    assert max(overflow) <= 0.01, f"a cell ends {max(overflow):.1f}mm past the right margin"


def test_expense_log_pdf_with_a_wide_portfolio(client, db_session, monkeypatch):
    """Same for the pivot table, whose narrower columns only overflow further out."""
    _seed_wide_portfolio(db_session, count=25)
    overflow = _track_right_edge(monkeypatch)

    resp = client.get("/reports/expense-log", params={"year": 2025})

    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"
    assert max(overflow) <= 0.01, f"a cell ends {max(overflow):.1f}mm past the right margin"


def test_income_expense_pdf(client, db_session):
    _seed_year(db_session)
    resp = client.get("/reports/income-expense", params={"year": 2025})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"


def test_income_expense_csv(client, db_session):
    _seed_year(db_session)
    resp = client.get("/reports/income-expense", params={"year": 2025, "format": "csv"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "Income & Expense Report" in resp.content.decode("utf-8-sig")


def test_expense_log_pdf(client, db_session):
    _seed_year(db_session)
    resp = client.get("/reports/expense-log", params={"year": 2025})
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_expense_log_csv(client, db_session):
    _seed_year(db_session)
    resp = client.get("/reports/expense-log", params={"year": 2025, "format": "csv"})
    assert resp.status_code == 200
    assert "Expense Log" in resp.content.decode("utf-8-sig")


def test_report_records_export_in_history(client, db_session):
    _seed_year(db_session)
    assert client.get("/reports/history").json() == []

    client.get("/reports/income-expense", params={"year": 2025, "format": "csv"})
    history = client.get("/reports/history").json()
    assert len(history) == 1
    assert history[0]["report_type"] == "income_expense"
    assert history[0]["format"] == "csv"
    assert history[0]["year"] == 2025


def test_report_invalid_format_rejected(client):
    resp = client.get("/reports/income-expense", params={"year": 2025, "format": "xml"})
    assert resp.status_code == 422


def test_report_year_out_of_range_rejected(client):
    assert client.get("/reports/income-expense", params={"year": 1800}).status_code == 422


def test_history_owner_scoped(client_factory, db_session):
    _seed_year(db_session, owner_id=OWNER_B)
    client_b = client_factory(OWNER_B)
    client_b.get("/reports/income-expense", params={"year": 2025, "format": "csv"})

    client_a = client_factory(OWNER_A)
    assert client_a.get("/reports/history").json() == []


def test_delete_history(client, db_session):
    _seed_year(db_session)
    client.get("/reports/income-expense", params={"year": 2025, "format": "csv"})
    export_id = client.get("/reports/history").json()[0]["id"]

    assert client.delete(f"/reports/history/{export_id}").status_code == 204
    assert client.get("/reports/history").json() == []


def test_delete_history_not_found(client):
    assert client.delete("/reports/history/999").status_code == 404


def test_delete_history_other_owner(client_factory, db_session):
    _seed_year(db_session, owner_id=OWNER_B)
    client_b = client_factory(OWNER_B)
    client_b.get("/reports/income-expense", params={"year": 2025, "format": "csv"})
    export_id = client_b.get("/reports/history").json()[0]["id"]

    client_a = client_factory(OWNER_A)
    assert client_a.delete(f"/reports/history/{export_id}").status_code == 404

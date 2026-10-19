"""Reading the Tax Authority's yearly booklet.

The fixtures are the 2026 booklet's own lines, as PDFium extracts them. Two builds extract
right-to-left text differently — in reading order, or in display order with each line's
words reversed — and the booklet has to read the same either way. The full files for
2022-2026 were checked against both builds when this was written."""
import pytest

from app.services import il_tax_source as il
from app.services.il_tax_source import BookletError, parse_booklet_text

# Reading order. The personal table, then the other-income one, which restarts at 31%; the
# top rate split across lines the way the 2024 and 2025 booklets print it.
READING = """
עד- 7,010 10% עד- 84,120 10%
מ- 7,011 עד- 10,060 14% מ- 84,121 עד- 120,720 14%
מ- 10,061 עד- 19,000 20% מ- 120,721 עד- 228,000 20%
מ- 19,001 עד- 25,100 31% מ- 228,001 עד- 301,200 31%
מ- 25,101 עד- 46,690 35% מ- 301,201 עד- 560,280 35%
מכל שקל
נוסף
 47%
עד- 25,100 31% עד- 301,200 31%
מ- 25,101 עד- 46,690 35% מ- 301,201 עד- 560,280 35%
מכל שקל נוסף 47%
תקרת הכנסה משכר דירה - פטור ממס 5,654 לחודש
נקודת זיכוי 33א 242 לחודש
על פי סעיף 121ב לפקודה, יחיד אשר הכנסתו החייבת בשנת המס עלתה על 721,560 ₪
"""

# The same lines in display order, as pypdfium2 5.13 on Windows returns them.
DISPLAY = """
10% 84,120 -עד 10% 7,010 -עד
14% 120,720 -עד 84,121 -מ 14% 10,060 -עד 7,011 -מ
20% 228,000 -עד 120,721 -מ 20% 19,000 -עד 10,061 -מ
31% 301,200 -עד 228,001 -מ 31% 25,100 -עד 19,001 -מ
35% 560,280 -עד 301,201 -מ 35% 46,690 -עד 25,101 -מ
47% נוסף שקל מכל
31% 301,200 -עד 31% 25,100 -עד
35% 560,280 -עד 301,201 -מ 35% 46,690 -עד 25,101 -מ
47% נוסף שקל מכל
לחודש 5,654 ממס פטור - דירה משכר הכנסה תקרת
לחודש 242 א33 זיכוי נקודת
₪ 721,560 על עלתה המס בשנת החייבת הכנסתו אשר יחיד ,לפקודה ב121 סעיף פי על
"""

EXPECTED = {
    "rent_exemption_ceiling_monthly": 5654,
    "personal_brackets": [
        [84120, 10], [120720, 14], [228000, 20], [301200, 31], [560280, 35], [None, 47]
    ],
    "other_income_brackets": [[301200, 31], [560280, 35], [None, 47]],
    "surtax_threshold": 721560,
    "credit_point_monthly": 242,
}


def test_reads_the_booklet_in_reading_order():
    assert parse_booklet_text(READING) == EXPECTED


def test_reads_the_booklet_in_display_order():
    with pytest.raises(BookletError):
        parse_booklet_text(DISPLAY)
    assert parse_booklet_text(il._reading_order(DISPLAY)) == EXPECTED


def test_read_booklet_falls_back_to_the_other_order(monkeypatch):
    monkeypatch.setattr(il, "_text_of", lambda pdf: DISPLAY)
    assert il.read_booklet(b"%PDF") == EXPECTED


def test_a_row_whose_monthly_and_annual_figures_disagree_is_refused():
    with pytest.raises(BookletError, match="does not add up"):
        parse_booklet_text(READING.replace("עד- 84,120 10%", "עד- 84,000 10%"))


def test_two_different_ceilings_are_refused():
    with pytest.raises(BookletError, match="exemption ceiling"):
        parse_booklet_text(READING + "\nשכר דירה פטור ממס 5,471 לחודש\n")


def test_a_missing_other_income_table_is_refused():
    personal_only = READING.split("עד- 25,100 31% עד- 301,200 31%")[0] + (
        "\nתקרת הכנסה משכר דירה - פטור ממס 5,654 לחודש"
    )
    with pytest.raises(BookletError, match="other-income"):
        parse_booklet_text(personal_only)


def test_a_file_that_is_not_a_pdf_is_refused():
    with pytest.raises(BookletError, match="not a readable PDF"):
        il.read_booklet(b"<!DOCTYPE html>")


def test_law_values_follow_the_year_they_took_effect():
    keys = {year: {v.key for v in il.law_values(year)} for year in (2022, 2023, 2025)}
    assert "capital_surtax_rate_pct" not in keys[2023]
    assert "capital_surtax_rate_pct" in keys[2025]
    assert "rent_paid_deduction_cap" not in keys[2022]
    assert "rent_paid_deduction_cap" in keys[2023]
    assert all(v.reference and v.url for v in il.LAW_VALUES)


def test_the_booklet_address_names_the_year():
    assert il.booklet_url(2026).endswith("monthly-deductions-booklet-2026.pdf")

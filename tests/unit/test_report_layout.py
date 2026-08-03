"""PDF table geometry: the pure helpers behind the report layout.

These guard the two structural defects the tables used to have — text drawn past its cell
border, and columns drawn past the right edge of the page — without pinning PDF bytes.
"""
import pytest

from app.services.report_service import FONT, FONT_FALLBACK, _PDF, _chunks


@pytest.fixture
def pdf():
    doc = _PDF("income_title", 2025, orientation="L", unit="mm", format="A4")
    doc.add_page()
    doc.set_font(FONT, "", 7)
    return doc


class TestFit:
    def test_short_text_is_returned_unchanged(self, pdf):
        assert pdf.fit("Rev", 22) == "Rev"

    def test_long_text_is_trimmed_to_the_cell(self, pdf):
        text = "42 Rothschild Boulevard, Tel Aviv-Yafo"
        fitted = pdf.fit(text, 22)

        assert fitted != text
        assert fitted.endswith("…")
        assert pdf.get_string_width(fitted) <= 22 - 2 * pdf.c_margin

    def test_hebrew_is_measured_not_counted(self, pdf):
        """Character slicing was the old bug: glyph widths differ per script."""
        fitted = pdf.fit("רחוב הרצל 12, תל אביב", 15)
        assert pdf.get_string_width(fitted) <= 15 - 2 * pdf.c_margin

    def test_six_digit_figures_fit_their_sub_column(self, pdf):
        """The width the generators size sub-columns from."""
        sub_w = pdf.get_string_width("999,999") + 2 * pdf.c_margin + 0.4
        assert pdf.fit("999,999", sub_w) == "999,999"

    def test_empty_text_survives(self, pdf):
        assert pdf.fit("", 22) == ""


class TestFontFallback:
    def test_hebrew_does_not_leave_the_fallback_font_selected(self, pdf):
        """Regression: every amount after a Hebrew label used to render as nothing.

        Drawing Hebrew switches `current_font` to the fallback family and leaves it there.
        Noto Sans Hebrew has no digits and no Latin, so unless the intended font is restored,
        the next cell's text is silently dropped — one Hebrew category name in the pivot's
        first column blanked every figure in its row.
        """
        pdf.cell(40, 5, "תיקונים")
        assert pdf.current_font.name == FONT_FALLBACK  # fpdf2 switched, as expected

        pdf.cell(20, 5, "123,456")
        assert pdf.current_font.name == FONT  # ...and we switched back before drawing

    def test_a_same_size_set_font_still_restores(self, pdf):
        """`set_font` with the family/style/size already recorded is a no-op in fpdf2, which
        is exactly why the fallback used to stick."""
        pdf.cell(40, 5, "תיקונים")
        pdf.set_font(FONT, "", 7)
        pdf.cell(20, 5, "Repairs")
        assert pdf.current_font.name == FONT


class TestColumnsPerBlock:
    def test_columns_never_exceed_the_page_width(self, pdf):
        fixed_w, col_w = 20, 35.0
        n = pdf.columns_per_block(fixed_w, col_w)

        assert fixed_w + n * col_w <= pdf.epw
        assert fixed_w + (n + 1) * col_w > pdf.epw  # and it is the most that fit

    def test_a_column_wider_than_the_page_still_draws_one(self, pdf):
        assert pdf.columns_per_block(20, pdf.epw * 2) == 1


class TestRightToLeft:
    """A Hebrew report mirrors its tables: the leading column sits against the right margin."""

    @pytest.fixture
    def he(self):
        doc = _PDF("income_title", 2025, lang="he", orientation="L", unit="mm", format="A4")
        doc.add_page()
        doc.set_font(FONT, "", 7)
        return doc

    def test_english_places_the_leading_column_on_the_left(self, pdf):
        widths = [50, 20, 30]
        assert pdf.col_x(widths, 0) == pdf.l_margin
        assert pdf.col_x(widths, 2) == pdf.l_margin + 70

    def test_hebrew_places_the_leading_column_on_the_right(self, he):
        widths = [50, 20, 30]
        assert he.col_x(widths, 0) == he.l_margin + 50  # 20 + 30 of later columns to its left
        assert he.col_x(widths, 2) == he.l_margin

    def test_a_row_covers_the_same_span_in_both_directions(self, pdf, he):
        cells = [(50, "a", {}), (20, "b", {}), (30, "c", {})]
        pdf.row(list(cells), 5)
        he.row(list(cells), 5)
        assert pdf.get_x() == he.get_x() == pdf.l_margin + 100

    def test_hebrew_titles_and_labels_are_translated(self, he):
        assert he.rtl is True
        assert he._title == "דוח הכנסות והוצאות"

    def test_an_unknown_language_falls_back_to_english(self):
        doc = _PDF("income_title", 2025, lang="fr", orientation="L", unit="mm", format="A4")
        assert doc.lang == "en"
        assert doc.rtl is False


class TestChunks:
    def test_splits_into_blocks_of_at_most_size(self):
        assert _chunks([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]

    def test_empty_input_still_yields_one_block(self):
        """An owner with no properties must still draw its (empty) table, not vanish."""
        assert _chunks([], 3) == [[]]

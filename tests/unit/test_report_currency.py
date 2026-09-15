"""How a report prints its currency.

The load-bearing test here is the first one: the Israeli report is the artifact users hand
to an accountant, and it has always printed a language-dependent label in the prefix
position. Changing that would silently alter every report they have ever filed.
"""
from decimal import Decimal

import pytest

from app.services import country_service
from app.services.report_service import _fmt


class TestIsraelIsUnchanged:
    def test_english_report_still_prints_the_iso_code_as_a_prefix(self):
        assert _fmt(Decimal("5000"), "en", country_service.effective_currency("IL")) == "ILS 5,000"

    def test_hebrew_report_still_prints_the_symbol_as_a_prefix(self):
        assert _fmt(Decimal("5000"), "he", country_service.effective_currency("IL")) == "₪5,000"

    def test_unknown_country_falls_back_to_the_israeli_form(self):
        """Every report that predates the currency parameter was Israeli."""
        assert _fmt(Decimal("5000"), "en", None) == "ILS 5,000"
        assert _fmt(Decimal("5000"), "he", None) == "₪5,000"


class TestOtherCurrencies:
    @pytest.mark.parametrize(
        "code,expected",
        [
            ("US", "$5,000"),
            ("GB", "£5,000"),
            ("AU", "$5,000"),
        ],
    )
    def test_prefix_currencies(self, code, expected):
        assert _fmt(Decimal("5000"), "en", country_service.effective_currency(code)) == expected

    def test_suffix_currencies(self):
        assert _fmt(Decimal("5000"), "en", country_service.effective_currency("FR")) == "5,000€"

    def test_currency_does_not_follow_the_reader_s_language(self):
        """A report is about a portfolio. Its currency does not change because someone
        switched the app to English."""
        us = country_service.effective_currency("US")
        assert _fmt(Decimal("5000"), "en", us) == _fmt(Decimal("5000"), "he", us)

    def test_a_currency_with_no_symbol_falls_back_to_its_iso_code(self):
        """Better a correct code than a wrong glyph."""
        out = _fmt(Decimal("5000"), "en", country_service.effective_currency("KE"))
        assert "KES" in out


class TestAChosenCurrency:
    """An account can pick a currency its country does not use. The report follows the
    account, because the report is about that portfolio's money."""

    def test_the_chosen_currency_is_what_prints(self):
        out = _fmt(Decimal("5000"), "en", country_service.effective_currency("IL", "USD"))
        assert out == "$5,000"

    def test_it_takes_the_currency_s_own_symbol_side_not_the_country_s(self):
        """Israel writes its symbol last, so an Israeli account holding dollars would
        otherwise read `5,000$` — which nobody writes."""
        assert _fmt(Decimal("5000"), "en", country_service.effective_currency("IL")) == "ILS 5,000"
        assert _fmt(Decimal("5000"), "en", country_service.effective_currency("IL", "USD")) == "$5,000"

    def test_an_unknown_choice_falls_back_to_the_country(self):
        chosen_nonsense = country_service.effective_currency("US", "XXX")
        assert _fmt(Decimal("5000"), "en", chosen_nonsense) == "$5,000"

    def test_report_amounts_stay_whole_even_for_a_zero_decimal_currency(self):
        """`decimals` caps the *screens*. A report has always printed round figures, and
        turning ₪5,000 into ₪5,000.00 is not a change to make quietly."""
        assert _fmt(Decimal("5000"), "en", country_service.effective_currency("JP")) == "¥5,000"

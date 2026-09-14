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
        assert _fmt(Decimal("5000"), "en", country_service.config_for("IL")) == "ILS 5,000"

    def test_hebrew_report_still_prints_the_symbol_as_a_prefix(self):
        assert _fmt(Decimal("5000"), "he", country_service.config_for("IL")) == "₪5,000"

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
        assert _fmt(Decimal("5000"), "en", country_service.config_for(code)) == expected

    def test_suffix_currencies(self):
        assert _fmt(Decimal("5000"), "en", country_service.config_for("FR")) == "5,000€"

    def test_currency_does_not_follow_the_reader_s_language(self):
        """A report is about a portfolio. Its currency does not change because someone
        switched the app to English."""
        us = country_service.config_for("US")
        assert _fmt(Decimal("5000"), "en", us) == _fmt(Decimal("5000"), "he", us)

    def test_a_currency_with_no_symbol_falls_back_to_its_iso_code(self):
        """Better a correct code than a wrong glyph."""
        out = _fmt(Decimal("5000"), "en", country_service.config_for("KE"))
        assert "KES" in out

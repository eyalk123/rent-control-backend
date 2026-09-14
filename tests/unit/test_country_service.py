"""Country config and capability resolution.

The tests that matter here are the invariants, not the data: that Israel is the only
country with capabilities, that an unknown code can never acquire them, and that labels
are keys rather than finished strings. Individual currency symbols are reference data and
are not asserted one by one — a wrong symbol is a copy fix, a wrong capability is a
corrupted ledger.
"""
import pytest

from app.countries.config import COUNTRIES
from app.services import country_service as cs


class TestCountryTable:
    def test_covers_the_iso_list(self):
        assert len(COUNTRIES) > 200
        for code, cfg in COUNTRIES.items():
            assert len(code) == 2 and code.isupper()
            assert cfg.country_code == code
            assert cfg.name

    def test_israel_is_the_only_native_tier(self):
        native = [c for c, cfg in COUNTRIES.items() if cfg.tier == "native"]
        assert native == ["IL"]

    def test_israel_is_the_only_country_with_any_capability(self):
        """A country added without being thought about must get the skimmed product.

        The failure this guards against is a new row quietly inheriting `cpi_linkage`,
        which would price its leases against Israeli index data.
        """
        for code, cfg in COUNTRIES.items():
            caps = cfg.capabilities
            enabled = any(
                [
                    caps.cpi_linkage,
                    caps.tax_tracks,
                    caps.israeli_property_types,
                    caps.bit_payments,
                    caps.structured_bank_details,
                ]
            )
            assert enabled == (code == "IL"), f"{code} has unexpected capabilities"

    def test_israel_formatting_is_unchanged_from_today(self):
        """Israel renders byte-for-byte as before, which is the primary regression risk."""
        il = COUNTRIES["IL"]
        assert il.currency == "ILS"
        assert il.currency_symbol == "₪"
        assert il.currency_symbol_position == "suffix"
        assert il.number_format == "1,234.56"
        assert il.date_format == "DMY"
        assert il.area_unit == "sqm"
        assert il.revenue_basis_default == "accrual"

    def test_israel_registry_labels_fall_through_to_the_locale_files(self):
        """None means "no override", so `t('property.block')` still resolves to Block/גוש.

        This is what makes the Israeli rendering path untouched code rather than
        re-verified code.
        """
        assert COUNTRIES["IL"].registry_key_1 is None
        assert COUNTRIES["IL"].registry_key_2 is None

    def test_registry_labels_are_i18n_keys_not_display_strings(self):
        """A finished string here would be untranslatable for e.g. a French-reading
        Israeli. Country picks the concept; the locale file says it."""
        for code, cfg in COUNTRIES.items():
            for key in (cfg.registry_key_1, cfg.registry_key_2):
                if key is not None:
                    assert key.startswith("property.registry."), f"{code}: {key}"
                    assert " " not in key

    @pytest.mark.parametrize(
        "code,expected",
        [("US", "MDY"), ("GB", "DMY"), ("IL", "DMY"), ("JP", "YMD")],
    )
    def test_date_formats(self, code, expected):
        assert COUNTRIES[code].date_format == expected

    def test_open_ended_countries_are_flagged_but_not_blocked(self):
        """The flag is informational. No country is ever unusable."""
        assert COUNTRIES["GB"].open_ended_tenancies is True
        assert COUNTRIES["IL"].open_ended_tenancies is False
        for cfg in COUNTRIES.values():
            assert cfg.tier in ("native", "supported")

    def test_us_defaults_to_cash_basis(self):
        assert COUNTRIES["US"].revenue_basis_default == "cash"
        assert COUNTRIES["IL"].revenue_basis_default == "accrual"


class TestResolution:
    def test_null_resolves_to_israel(self):
        """A row written before the column existed was Israeli by definition, so a value
        that escapes the backfill still renders as it did before."""
        assert cs.config_for(None).country_code == "IL"
        assert cs.capabilities_for(None).cpi_linkage is True

    def test_unknown_code_resolves_to_the_neutral_fallback(self):
        """Deliberately *not* Israel: handing an unrecognised country the Israeli feature
        set would price a lease against index data that does not apply to it."""
        caps = cs.capabilities_for("XX")
        assert caps.cpi_linkage is False
        assert caps.israeli_property_types is False
        assert cs.config_for("XX").tier == "supported"

    @pytest.mark.parametrize("raw", ["il", " IL ", "Il"])
    def test_codes_are_normalized(self, raw):
        assert cs.config_for(raw).country_code == "IL"

    def test_blank_is_treated_as_missing(self):
        assert cs.normalize("   ") is None
        assert cs.config_for("").country_code == "IL"

    def test_is_known_rejects_junk_but_accepts_real_codes(self):
        assert cs.is_known("us") is True
        assert cs.is_known("XX") is False
        assert cs.is_known(None) is False

    def test_all_countries_is_name_sorted_for_the_picker(self):
        names = [c.name for c in cs.all_countries()]
        assert names == sorted(names)
        assert len(names) == len(COUNTRIES)


class TestDialCodes:
    def test_every_country_has_one(self):
        """A missing code makes the client leave the number exactly as typed, which is the
        old broken behaviour. There is no country where that is the right answer."""
        missing = [c for c, cfg in COUNTRIES.items() if not cfg.dial_code]
        assert missing == []

    def test_they_are_bare_digits(self):
        """No '+', no spaces — wa.me takes the digits only, and a stray character would be
        silently stripped into a different number."""
        for code, cfg in COUNTRIES.items():
            assert cfg.dial_code.isdigit(), f"{code}: {cfg.dial_code!r}"

    @pytest.mark.parametrize(
        "code,expected",
        [("IL", "972"), ("US", "1"), ("CA", "1"), ("GB", "44"), ("FR", "33"), ("IN", "91")],
    )
    def test_known_codes(self, code, expected):
        assert COUNTRIES[code].dial_code == expected

    def test_israel_is_unchanged(self):
        """The mobile client hardcoded 972 before this existed; it must still resolve to it."""
        assert cs.config_for("IL").dial_code == "972"
        assert cs.config_for(None).dial_code == "972"

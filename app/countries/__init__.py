"""Per-country reference data and capability flags.

Reference data, not user data: it changes with a release, so it lives in code and is
reviewed in a pull request rather than sitting in a table that needs a migration every
time a currency symbol is corrected. Both clients need the same values to format money
and dates, so it is served to them over HTTP (``GET /countries``).

Read it through :mod:`app.services.country_service`, never by importing ``COUNTRIES``
directly — the service is the single accessor, and it is what makes "rules read the
property's country" true everywhere.
"""
from app.countries.config import COUNTRIES, Capabilities, CountryConfig, DEFAULT_COUNTRY

__all__ = ["COUNTRIES", "Capabilities", "CountryConfig", "DEFAULT_COUNTRY"]

"""The single accessor for per-country config and capability flags.

Everything that needs to know what a country does goes through here rather than importing
``COUNTRIES`` directly. Two reasons:

**It is where the fallback lives.** An unknown or missing country code resolves to a
usable config instead of raising — a stored value can always be ``NULL`` (a row written
before the column existed, or an owner who has not reached the country gate yet), and a
500 on a property page is a much worse answer than the skimmed defaults.

**It is what makes "rules read the property, not the account" true.** Call
``capabilities_for(property.country)`` and the day a foreign property exists nothing has
to be migrated. Call ``COUNTRIES[owner.country]`` at the call site and it does.
"""
from app.countries.config import COUNTRIES, DEFAULT_COUNTRY, Capabilities, CountryConfig

# What an unrecognised code resolves to. Deliberately *not* Israel: an unknown country
# must never be handed the Israeli feature set, because there is no index data behind it.
# Every capability is off and every format is the neutral default.
_FALLBACK = CountryConfig(country_code="ZZ", name="Unknown")


def normalize(country_code: str | None) -> str | None:
    """Upper-case a stored code, or ``None`` if there isn't one worth reading."""
    if not country_code:
        return None
    code = country_code.strip().upper()
    return code or None


def config_for(country_code: str | None) -> CountryConfig:
    """The config for a country. Never raises.

    ``NULL`` and *unrecognised* resolve differently, on purpose — they are not the same
    situation:

    - ``NULL`` means a row written before this column existed, and every one of those was
      Israeli by definition, so it resolves to Israel. That is what keeps an existing
      account byte-for-byte unchanged if any row escapes the backfill.
    - An *unrecognised* code means something is wrong — a typo, a retired ISO code, a
      client sending junk. It resolves to the neutral fallback with every capability off,
      because handing an unknown country the Israeli feature set would price a lease
      against index data that does not apply to it.

    Guessing wrong in the first case costs nothing; guessing wrong in the second corrupts
    a ledger. Hence the asymmetry.
    """
    code = normalize(country_code)
    if code is None:
        return COUNTRIES[DEFAULT_COUNTRY]
    return COUNTRIES.get(code, _FALLBACK)


def capabilities_for(country_code: str | None) -> Capabilities:
    """What this country's accounts may do. The thing conditionals should read."""
    return config_for(country_code).capabilities


def is_known(country_code: str | None) -> bool:
    """Whether the code is in the ISO table — for validating user input, not for reads."""
    code = normalize(country_code)
    return code is not None and code in COUNTRIES


def countries_with_index_linkage() -> list[str]:
    """Country codes whose accounts have a real index source behind them.

    Derived from the capability flags rather than written down, so adding a second market
    is one flag rather than a list someone has to remember to update. Today this is Israel
    alone; the day an INE or INSEE adapter lands, flipping that country's flag scopes the
    job to it automatically.
    """
    return [code for code, cfg in COUNTRIES.items() if cfg.capabilities.cpi_linkage]


def all_countries() -> list[CountryConfig]:
    """Every country, name-ordered — what ``GET /countries`` serves to the signup picker."""
    return sorted(COUNTRIES.values(), key=lambda c: c.name)

"""How an amount is written out, given a country's conventions.

Shared by every artefact that prints money server-side — the PDF reports and the chat
agent — because they were each getting it wrong in their own way. Python's ``:,`` is not
locale-aware: it always writes ``1,234.56`` whatever the account is, so a Spanish landlord
saw ``2.289 €`` on screen and ``2,289€`` in the PDF their accountant received, and the
assistant answered every question in shekels regardless of country.

Two rules live here and nowhere else:

**Grouping** comes from the country's ``number_format``, one of the three styles the country
table declares. **Spacing** comes from the country's ``currency_symbol_spaced``, and only
ever qualifies a suffix — a prefix symbol is written tight in every locale.

Both default to Israel's exact current behaviour, so a call that passes neither is what the
app has always done rather than a neutral guess.
"""
from __future__ import annotations

from decimal import Decimal

from app.services import country_service

#: The style an account that has not been resolved yet is written in. Israel's, and the
#: app's behaviour before any of this existed.
DEFAULT_NUMBER_FORMAT = "1,234.56"

#: (thousands, decimal) for each style the country table can declare.
_SEPARATORS: dict[str, tuple[str, str]] = {
    "1,234.56": (",", "."),
    "1.234,56": (".", ","),
    "1 234,56": (" ", ","),
}


def group(
    amount: Decimal | float | int,
    number_format: str = DEFAULT_NUMBER_FORMAT,
    decimals: int = 0,
) -> str:
    """A bare number with the account's separators. No currency.

    Translated in **one pass** via ``str.maketrans`` rather than two ``replace`` calls: with
    decimals, ``1,234.56`` has to become ``1.234,56``, and replacing commas then dots would
    turn every separator into the same character.
    """
    text = f"{amount:,.{decimals}f}"
    thousands, decimal_point = _SEPARATORS.get(number_format, _SEPARATORS[DEFAULT_NUMBER_FORMAT])
    if (thousands, decimal_point) == (",", "."):
        return text
    return text.translate(str.maketrans({",": thousands, ".": decimal_point}))


def format_money(
    amount: Decimal | float | int | None,
    currency: country_service.EffectiveCurrency | None = None,
    number_format: str = DEFAULT_NUMBER_FORMAT,
    symbol_spaced: bool = False,
) -> str:
    """An amount with its symbol, the way this account writes money.

    Decimals are shown only when the amount actually has them — ``12,000`` stays whole and
    ``12,000.50`` keeps its cents. That is the rule the agent has always used, kept because
    an assistant reading out ``₪12,000.00`` for a round rent sounds like a machine.

    ``None`` for ``currency`` resolves to Israel, the same fallback ``report_service._fmt``
    uses, which is what keeps an account with no owner row rendering as it always did.
    """
    effective = currency or country_service.effective_currency(None)
    if amount is None:
        return _join("0", effective, symbol_spaced)

    value = Decimal(str(amount))
    decimals = 0 if value == value.to_integral_value() else 2
    return _join(group(value, number_format, decimals), effective, symbol_spaced)


def _join(
    text: str,
    currency: country_service.EffectiveCurrency,
    symbol_spaced: bool,
) -> str:
    if currency.symbol_position != "suffix":
        return f"{currency.symbol}{text}"
    # An ordinary space, not U+00A0: this string is read aloud, embedded in PDF cells and
    # quoted back by a language model, none of which benefit from a no-break space, and one
    # of which would have to carry the glyph in an embedded font. The clients use U+00A0
    # because they wrap; the two render identically.
    return f"{text} {currency.symbol}" if symbol_spaced else f"{text}{currency.symbol}"

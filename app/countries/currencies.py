"""The currency table: one row per ISO 4217 code any country in the table uses.

Split out of ``config.py`` when currency stopped being a pure function of country. A
landlord can now choose one — a Swiss owner letting in euros, an Israeli whose lease is
written in dollars — so the currency needs a name to show in a picker, and a home that is
not the country row.

Three fields exist only because a user can now pick a currency their country does not use:

**name** — a picker needs "Euro", not ``EUR``.

**decimals** — used as a *cap*, never as a minimum. JPY and KRW have no minor unit, so
``¥1,234.00`` is not a rounding difference, it is a fraction of a thing that does not
exist. Everything else keeps showing what it showed.

**default_symbol_position** — consulted *only* when the chosen currency is not the
country's own. Position is genuinely a property of the reader, not of the money: ``€1,234``
in Ireland and ``1.234 €`` in Germany are the same currency written two ways, and the
country row gets that right. It is when the two come apart — an Israeli account holding
dollars, where the country says suffix and nobody writes ``1,234$`` — that the currency has
to have an opinion. See ``country_service.effective_currency``.

**A currency with no glyph carries its own code as its symbol.** ``XOF 1,234`` is correct
if plain, and it is what the country table already did for the ~100 codes with no symbol.
That is what lets the picker list every currency rather than only the pretty ones, so a
landlord in Dakar or Lusaka can name their own money.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SymbolPosition = Literal["prefix", "suffix"]


@dataclass(frozen=True)
class Currency:
    code: str
    name: str
    symbol: str
    #: Maximum fraction digits to display. A cap, not a minimum.
    decimals: int = 2
    #: Where the symbol goes when the country has no say — see the module docstring.
    default_symbol_position: SymbolPosition = "prefix"


# ─────────────────────────── source table ───────────────────────────
#
# code | name | symbol ("" == use the code) | decimals | position
#
# Hand-checked for every currency with a glyph. The rest are generated names from the ISO
# 4217 register and carry no symbol, which is the safe default rather than a guessed one.

_TABLE = """
AED|UAE dirham|د.إ|2|prefix
AFN|Afghan afghani|؋|2|prefix
ALL|Albanian lek||2|suffix
AMD|Armenian dram|֏|2|suffix
AOA|Angolan kwanza||2|prefix
ARS|Argentine peso|$|2|prefix
AUD|Australian dollar|$|2|prefix
AWG|Aruban florin||2|prefix
AZN|Azerbaijani manat|₼|2|suffix
BAM|Bosnia-Herzegovina convertible mark||2|suffix
BBD|Barbadian dollar|$|2|prefix
BDT|Bangladeshi taka|৳|2|prefix
BGN|Bulgarian lev|лв|2|suffix
BHD|Bahraini dinar||3|prefix
BIF|Burundian franc||0|prefix
BMD|Bermudian dollar|$|2|prefix
BND|Brunei dollar|$|2|prefix
BOB|Bolivian boliviano||2|prefix
BRL|Brazilian real|R$|2|prefix
BSD|Bahamian dollar|$|2|prefix
BTN|Bhutanese ngultrum||2|prefix
BWP|Botswana pula||2|prefix
BYN|Belarusian ruble||2|suffix
BZD|Belize dollar|$|2|prefix
CAD|Canadian dollar|$|2|prefix
CDF|Congolese franc||2|prefix
CHF|Swiss franc|CHF|2|prefix
CLP|Chilean peso|$|0|prefix
CNY|Chinese yuan|¥|2|prefix
COP|Colombian peso|$|2|prefix
CRC|Costa Rican colon|₡|2|prefix
CUP|Cuban peso|$|2|prefix
CVE|Cape Verdean escudo||2|suffix
CZK|Czech koruna|Kč|2|suffix
DJF|Djiboutian franc||0|prefix
DKK|Danish krone|kr|2|suffix
DOP|Dominican peso|$|2|prefix
DZD|Algerian dinar||2|prefix
EGP|Egyptian pound|E£|2|prefix
ERN|Eritrean nakfa||2|prefix
ETB|Ethiopian birr||2|prefix
EUR|Euro|€|2|suffix
FJD|Fijian dollar|$|2|prefix
FKP|Falkland Islands pound|£|2|prefix
GBP|Pound sterling|£|2|prefix
GEL|Georgian lari|₾|2|suffix
GHS|Ghanaian cedi|₵|2|prefix
GIP|Gibraltar pound|£|2|prefix
GMD|Gambian dalasi||2|prefix
GNF|Guinean franc||0|prefix
GTQ|Guatemalan quetzal||2|prefix
GYD|Guyanese dollar|$|2|prefix
HKD|Hong Kong dollar|$|2|prefix
HNL|Honduran lempira||2|prefix
HTG|Haitian gourde||2|prefix
HUF|Hungarian forint|Ft|0|suffix
IDR|Indonesian rupiah|Rp|2|prefix
ILS|Israeli new shekel|₪|2|suffix
INR|Indian rupee|₹|2|prefix
IQD|Iraqi dinar||3|prefix
IRR|Iranian rial||2|prefix
ISK|Icelandic krona|kr|0|suffix
JMD|Jamaican dollar|$|2|prefix
JOD|Jordanian dinar||3|prefix
JPY|Japanese yen|¥|0|prefix
KES|Kenyan shilling||2|prefix
KGS|Kyrgyzstani som||2|suffix
KHR|Cambodian riel|៛|2|prefix
KMF|Comorian franc||0|prefix
KPW|North Korean won|₩|2|prefix
KRW|South Korean won|₩|0|prefix
KWD|Kuwaiti dinar||3|prefix
KYD|Cayman Islands dollar|$|2|prefix
KZT|Kazakhstani tenge|₸|2|prefix
LAK|Lao kip|₭|2|prefix
LBP|Lebanese pound||2|prefix
LKR|Sri Lankan rupee|₨|2|prefix
LRD|Liberian dollar|$|2|prefix
LSL|Lesotho loti||2|prefix
LYD|Libyan dinar||3|prefix
MAD|Moroccan dirham||2|suffix
MDL|Moldovan leu||2|suffix
MGA|Malagasy ariary||2|prefix
MKD|Macedonian denar||2|suffix
MMK|Myanmar kyat||2|prefix
MNT|Mongolian tugrik|₮|2|prefix
MOP|Macanese pataca||2|prefix
MRU|Mauritanian ouguiya||2|prefix
MUR|Mauritian rupee|₨|2|prefix
MVR|Maldivian rufiyaa||2|prefix
MWK|Malawian kwacha||2|prefix
MXN|Mexican peso|$|2|prefix
MYR|Malaysian ringgit|RM|2|prefix
MZN|Mozambican metical||2|prefix
NAD|Namibian dollar|$|2|prefix
NGN|Nigerian naira|₦|2|prefix
NIO|Nicaraguan cordoba||2|prefix
NOK|Norwegian krone|kr|2|suffix
NPR|Nepalese rupee|₨|2|prefix
NZD|New Zealand dollar|$|2|prefix
OMR|Omani rial||3|prefix
PAB|Panamanian balboa||2|prefix
PEN|Peruvian sol|S/|2|prefix
PGK|Papua New Guinean kina||2|prefix
PHP|Philippine peso|₱|2|prefix
PKR|Pakistani rupee|₨|2|prefix
PLN|Polish zloty|zł|2|suffix
PYG|Paraguayan guarani|₲|0|prefix
QAR|Qatari riyal||2|prefix
RON|Romanian leu|lei|2|suffix
RSD|Serbian dinar||2|suffix
RUB|Russian ruble|₽|2|suffix
RWF|Rwandan franc||0|prefix
SAR|Saudi riyal|﷼|2|prefix
SBD|Solomon Islands dollar|$|2|prefix
SCR|Seychellois rupee|₨|2|prefix
SDG|Sudanese pound||2|prefix
SEK|Swedish krona|kr|2|suffix
SGD|Singapore dollar|$|2|prefix
SHP|Saint Helena pound|£|2|prefix
SLE|Sierra Leonean leone||2|prefix
SOS|Somali shilling||2|prefix
SRD|Surinamese dollar|$|2|prefix
SSP|South Sudanese pound|£|2|prefix
STN|Sao Tome and Principe dobra||2|suffix
SYP|Syrian pound||2|prefix
SZL|Swazi lilangeni||2|prefix
THB|Thai baht|฿|2|prefix
TJS|Tajikistani somoni||2|suffix
TMT|Turkmenistani manat||2|suffix
TND|Tunisian dinar||3|prefix
TOP|Tongan pa'anga||2|prefix
TRY|Turkish lira|₺|2|suffix
TTD|Trinidad and Tobago dollar|$|2|prefix
TWD|New Taiwan dollar|NT$|2|prefix
TZS|Tanzanian shilling||2|prefix
UAH|Ukrainian hryvnia|₴|2|suffix
UGX|Ugandan shilling||0|prefix
USD|US dollar|$|2|prefix
UYU|Uruguayan peso|$|2|prefix
UZS|Uzbekistani som||2|suffix
VES|Venezuelan bolivar||2|prefix
VND|Vietnamese dong|₫|0|suffix
VUV|Vanuatu vatu||0|prefix
WST|Samoan tala||2|prefix
XAF|Central African CFA franc||0|suffix
XCD|East Caribbean dollar|$|2|prefix
XCG|Caribbean guilder||2|prefix
XOF|West African CFA franc||0|suffix
XPF|CFP franc||0|suffix
YER|Yemeni rial||2|prefix
ZAR|South African rand|R|2|prefix
ZMW|Zambian kwacha||2|prefix
ZWG|Zimbabwe gold||2|prefix
"""


def _build() -> dict[str, Currency]:
    out: dict[str, Currency] = {}
    for line in _TABLE.strip().splitlines():
        code, name, symbol, decimals, position = line.split("|")
        out[code] = Currency(
            code=code,
            name=name,
            # No glyph means the code is the symbol — correct if plain, and what the
            # country table has always fallen back to.
            symbol=symbol or code,
            decimals=int(decimals),
            default_symbol_position=position,  # type: ignore[arg-type]
        )
    return out


CURRENCIES: dict[str, Currency] = _build()


def symbol_for(code: str) -> str:
    """The display symbol for an ISO 4217 code, or the code itself.

    Kept as a function because ``config.py`` calls it while building its own rows, and an
    unknown code there must degrade rather than raise — a country row is built from a
    static table, but nothing guarantees the two tables never drift.
    """
    currency = CURRENCIES.get(code)
    return currency.symbol if currency else code


def get(code: str | None) -> Currency | None:
    """One currency, or ``None`` for an unknown or missing code. Never raises."""
    if not code:
        return None
    return CURRENCIES.get(code.strip().upper())


def all_currencies() -> list[Currency]:
    """Every currency, name-ordered — what ``GET /currencies`` serves to the picker."""
    return sorted(CURRENCIES.values(), key=lambda c: c.name)

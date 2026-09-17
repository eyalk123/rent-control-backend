"""The country table: one row per ISO 3166-1 alpha-2 code.

Built from a compact source table plus rule sets, rather than 249 hand-written rows. The
rule sets are the reviewable part — "which countries write dates month-first" is a claim
someone can check, where a wall of near-identical rows is not.

Two invariants this file exists to hold:

**No code branches on a country code.** Everything downstream reads a capability flag or a
config field. ``if country == "IL"`` scattered through three repos is how this becomes
unmaintainable by the third country.

**Labels are i18n keys, never finished strings.** A label depends on two independent
things — the country decides *which concept applies* (Block vs Title number vs Cadastral
reference), the language decides *how to say it*. Storing ``"Block"`` here conflates them
and breaks on a case that already exists: an Israeli landlord reading the app in French.
So ``registry_key_1`` holds ``"property.registry.title_number"`` and the client translates
it. Adding a language never reopens this file; it costs one translation per *concept*
(about six), not one per country.

``None`` on a registry key means "no override — fall back to the client's own translation
files". Israel uses it, which is why the Israeli rendering path is untouched code rather
than re-verified code: ``t('property.block')`` still resolves to Block / גוש exactly as
before. It is also the right default for a country nobody has hand-checked — generic, and
never wrong.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from app.countries import currencies

Tier = Literal["native", "supported"]
DateFormat = Literal["DMY", "MDY", "YMD"]
NumberFormat = Literal["1,234.56", "1.234,56", "1 234,56"]
AreaUnit = Literal["sqm", "sqft"]
RevenueBasis = Literal["accrual", "cash"]
SymbolPosition = Literal["prefix", "suffix"]

DEFAULT_COUNTRY = "IL"


@dataclass(frozen=True)
class Capabilities:
    """What a country's account may actually do.

    Every flag defaults to off. Israel is the only country that turns any of them on, so a
    country added without being thought about gets the skimmed product rather than an
    Israeli feature with no data behind it.

    A flag that is off must be off *end to end*: hidden in both clients, rejected by the
    API, and skipped by background jobs. A hidden control with a live endpoint is a bug.
    """

    cpi_linkage: bool = False
    tax_tracks: bool = False
    israeli_property_types: bool = False
    bit_payments: bool = False
    structured_bank_details: bool = False


@dataclass(frozen=True)
class CountryConfig:
    country_code: str
    name: str
    tier: Tier = "supported"
    # Informational only — never blocks signup. Drives one inline note on the renter form
    # and a longer default contract term, because the lease model cannot express "no end
    # date" and saying so beats letting the user discover it.
    open_ended_tenancies: bool = False

    currency: str = "USD"
    currency_symbol: str = "$"
    currency_symbol_position: SymbolPosition = "prefix"
    locale: str = "en"
    date_format: DateFormat = "DMY"
    number_format: NumberFormat = "1,234.56"
    area_unit: AreaUnit = "sqm"
    revenue_basis_default: RevenueBasis = "accrual"
    default_comms_channel: str = "whatsapp"
    # E.164 calling code, for defaulting a phone stored without one. "" means unknown,
    # which makes the clients leave the number exactly as typed rather than guess.
    dial_code: str = ""
    has_postal_codes: bool = True

    # i18n keys, never display strings. None == use the client's own translation files.
    registry_key_1: str | None = None
    registry_key_2: str | None = None

    capabilities: Capabilities = field(default_factory=Capabilities)


# ─────────────────────────── source table ───────────────────────────
#
# code | name | ISO 4217. Everything else is derived by the rule sets below.

_ISO_TABLE = """
AD Andorra EUR
AE United Arab Emirates AED
AF Afghanistan AFN
AG Antigua and Barbuda XCD
AI Anguilla XCD
AL Albania ALL
AM Armenia AMD
AO Angola AOA
AQ Antarctica USD
AR Argentina ARS
AS American Samoa USD
AT Austria EUR
AU Australia AUD
AW Aruba AWG
AX Aland Islands EUR
AZ Azerbaijan AZN
BA Bosnia and Herzegovina BAM
BB Barbados BBD
BD Bangladesh BDT
BE Belgium EUR
BF Burkina Faso XOF
BG Bulgaria BGN
BH Bahrain BHD
BI Burundi BIF
BJ Benin XOF
BL Saint Barthelemy EUR
BM Bermuda BMD
BN Brunei BND
BO Bolivia BOB
BQ Caribbean Netherlands USD
BR Brazil BRL
BS Bahamas BSD
BT Bhutan BTN
BV Bouvet Island NOK
BW Botswana BWP
BY Belarus BYN
BZ Belize BZD
CA Canada CAD
CC Cocos (Keeling) Islands AUD
CD Congo (Kinshasa) CDF
CF Central African Republic XAF
CG Congo (Brazzaville) XAF
CH Switzerland CHF
CI Cote d'Ivoire XOF
CK Cook Islands NZD
CL Chile CLP
CM Cameroon XAF
CN China CNY
CO Colombia COP
CR Costa Rica CRC
CU Cuba CUP
CV Cabo Verde CVE
CW Curacao XCG
CX Christmas Island AUD
CY Cyprus EUR
CZ Czechia CZK
DE Germany EUR
DJ Djibouti DJF
DK Denmark DKK
DM Dominica XCD
DO Dominican Republic DOP
DZ Algeria DZD
EC Ecuador USD
EE Estonia EUR
EG Egypt EGP
EH Western Sahara MAD
ER Eritrea ERN
ES Spain EUR
ET Ethiopia ETB
FI Finland EUR
FJ Fiji FJD
FK Falkland Islands FKP
FM Micronesia USD
FO Faroe Islands DKK
FR France EUR
GA Gabon XAF
GB United Kingdom GBP
GD Grenada XCD
GE Georgia GEL
GF French Guiana EUR
GG Guernsey GBP
GH Ghana GHS
GI Gibraltar GIP
GL Greenland DKK
GM Gambia GMD
GN Guinea GNF
GP Guadeloupe EUR
GQ Equatorial Guinea XAF
GR Greece EUR
GS South Georgia GBP
GT Guatemala GTQ
GU Guam USD
GW Guinea-Bissau XOF
GY Guyana GYD
HK Hong Kong HKD
HM Heard and McDonald Islands AUD
HN Honduras HNL
HR Croatia EUR
HT Haiti HTG
HU Hungary HUF
ID Indonesia IDR
IE Ireland EUR
IL Israel ILS
IM Isle of Man GBP
IN India INR
IO British Indian Ocean Territory USD
IQ Iraq IQD
IR Iran IRR
IS Iceland ISK
IT Italy EUR
JE Jersey GBP
JM Jamaica JMD
JO Jordan JOD
JP Japan JPY
KE Kenya KES
KG Kyrgyzstan KGS
KH Cambodia KHR
KI Kiribati AUD
KM Comoros KMF
KN Saint Kitts and Nevis XCD
KP North Korea KPW
KR South Korea KRW
KW Kuwait KWD
KY Cayman Islands KYD
KZ Kazakhstan KZT
LA Laos LAK
LB Lebanon LBP
LC Saint Lucia XCD
LI Liechtenstein CHF
LK Sri Lanka LKR
LR Liberia LRD
LS Lesotho LSL
LT Lithuania EUR
LU Luxembourg EUR
LV Latvia EUR
LY Libya LYD
MA Morocco MAD
MC Monaco EUR
MD Moldova MDL
ME Montenegro EUR
MF Saint Martin EUR
MG Madagascar MGA
MH Marshall Islands USD
MK North Macedonia MKD
ML Mali XOF
MM Myanmar MMK
MN Mongolia MNT
MO Macao MOP
MP Northern Mariana Islands USD
MQ Martinique EUR
MR Mauritania MRU
MS Montserrat XCD
MT Malta EUR
MU Mauritius MUR
MV Maldives MVR
MW Malawi MWK
MX Mexico MXN
MY Malaysia MYR
MZ Mozambique MZN
NA Namibia NAD
NC New Caledonia XPF
NE Niger XOF
NF Norfolk Island AUD
NG Nigeria NGN
NI Nicaragua NIO
NL Netherlands EUR
NO Norway NOK
NP Nepal NPR
NR Nauru AUD
NU Niue NZD
NZ New Zealand NZD
OM Oman OMR
PA Panama PAB
PE Peru PEN
PF French Polynesia XPF
PG Papua New Guinea PGK
PH Philippines PHP
PK Pakistan PKR
PL Poland PLN
PM Saint Pierre and Miquelon EUR
PN Pitcairn NZD
PR Puerto Rico USD
PS Palestine ILS
PT Portugal EUR
PW Palau USD
PY Paraguay PYG
QA Qatar QAR
RE Reunion EUR
RO Romania RON
RS Serbia RSD
RU Russia RUB
RW Rwanda RWF
SA Saudi Arabia SAR
SB Solomon Islands SBD
SC Seychelles SCR
SD Sudan SDG
SE Sweden SEK
SG Singapore SGD
SH Saint Helena SHP
SI Slovenia EUR
SJ Svalbard and Jan Mayen NOK
SK Slovakia EUR
SL Sierra Leone SLE
SM San Marino EUR
SN Senegal XOF
SO Somalia SOS
SR Suriname SRD
SS South Sudan SSP
ST Sao Tome and Principe STN
SV El Salvador USD
SX Sint Maarten XCG
SY Syria SYP
SZ Eswatini SZL
TC Turks and Caicos Islands USD
TD Chad XAF
TF French Southern Territories EUR
TG Togo XOF
TH Thailand THB
TJ Tajikistan TJS
TK Tokelau NZD
TL Timor-Leste USD
TM Turkmenistan TMT
TN Tunisia TND
TO Tonga TOP
TR Turkiye TRY
TT Trinidad and Tobago TTD
TV Tuvalu AUD
TW Taiwan TWD
TZ Tanzania TZS
UA Ukraine UAH
UG Uganda UGX
UM US Minor Outlying Islands USD
US United States USD
UY Uruguay UYU
UZ Uzbekistan UZS
VA Holy See EUR
VC Saint Vincent and the Grenadines XCD
VE Venezuela VES
VG British Virgin Islands USD
VI US Virgin Islands USD
VN Vietnam VND
VU Vanuatu VUV
WF Wallis and Futuna XPF
WS Samoa WST
YE Yemen YER
YT Mayotte EUR
ZA South Africa ZAR
ZM Zambia ZMW
ZW Zimbabwe ZWG
"""

# ─────────────────────────── rule sets ───────────────────────────
#
# Each is a claim someone can check. Anything not listed takes the default.

#: Codes present in the ISO source table that are **not offered as a country**.
#:
#: The table above is a verbatim ISO 3166-1 dump, kept that way so it stays checkable
#: against the standard rather than becoming a hand-curated list nobody can audit. This is
#: where the list stops being the standard and starts being a product decision, stated once
#: and in the open: a row removed from the table itself would be re-added by the next person
#: who diffed it against ISO and found it short.
#:
#: ``PS`` is excluded by product decision.
#:
#: Excluding a code is only safe while no account is stored with it. ``country_service``
#: falls back rather than raising for an unknown code, so an existing account would keep
#: working — but it would silently render as the fallback, which is not a thing to do to
#: someone quietly. Check before adding to this set.
_EXCLUDED = {"PS"}

#: Month-first dates. The US and the territories that follow its conventions.
_MDY = {"US", "PR", "GU", "AS", "MP", "VI", "UM", "FM", "MH", "PW"}

#: Year-first dates.
_YMD = {"CN", "JP", "KR", "KP", "TW", "HU", "MN", "LT", "BT"}

#: 1.234,56 — dot groups, comma decimal.
_COMMA_DECIMAL = {
    "DE", "AT", "NL", "BE", "LU", "ES", "IT", "PT", "GR", "DK", "IS", "TR", "ID", "VN",
    "BR", "AR", "CO", "CL", "VE", "UY", "PY", "BO", "HR", "SI", "RS", "BA", "ME", "MK",
    "RO", "BG", "AL", "MD", "CU", "CR", "MZ", "AO", "VA", "SM", "MC", "AD", "MT", "CY",
}

#: 1 234,56 — space groups, comma decimal.
_SPACE_DECIMAL = {
    "FR", "PL", "CZ", "SK", "SE", "NO", "FI", "UA", "RU", "LV", "EE", "BY", "KZ",
    "MA", "TN", "DZ", "GF", "GP", "MQ", "RE", "YT", "NC", "PF", "WF", "BL", "MF", "PM",
}

#: Floor area in square feet rather than m².
_SQFT = {"US", "CA", "GB", "IN", "PK", "BD", "HK", "SG", "MY", "PH", "LR", "MM"}

#: Revenue recognised when received rather than when it was due. US landlords file
#: Schedule E on the cash basis; everywhere else defaults to accrual, as today.
_CASH_BASIS = {"US"}

#: Currency symbol written after the amount.
_SUFFIX_CURRENCY = {
    "IL", "DE", "AT", "NL", "BE", "LU", "ES", "IT", "PT", "GR", "FI", "FR", "EE", "LV",
    "LT", "SK", "SI", "CY", "MT", "HR", "SE", "NO", "DK", "IS", "PL", "CZ", "HU", "RO",
    "BG", "RU", "UA", "TR", "VN", "AL", "MD", "RS", "BA", "MK", "ME", "AD", "MC", "SM",
    "VA", "GF", "GP", "MQ", "RE", "YT",
}

#: No national postal-code system, so the field must never be required.
_NO_POSTAL_CODES = {
    "AE", "AG", "AO", "AW", "BF", "BI", "BJ", "BS", "BW", "BZ", "CD", "CF", "CG", "CI",
    "CK", "CM", "CW", "DJ", "DM", "ER", "FJ", "GA", "GD", "GH", "GM", "GQ", "GY", "HK",
    "IE", "JM", "KI", "KM", "KN", "KP", "LC", "LY", "ML", "MO", "MR", "MS", "MU", "MW",
    "NR", "NU", "PA", "QA", "RW", "SB", "SC", "SL", "SO", "SR", "ST", "SX", "SY", "TC",
    "TD", "TF", "TG", "TK", "TL", "TO", "TT", "TV", "TZ", "UG", "VU", "WS", "YE", "ZW",
}

#: Residential tenancies here are typically open-ended — no end date, rolling until an
#: event stops them. The lease model cannot express that, so these accounts get one
#: honest line on the renter form and a longer default term. Nothing is blocked.
#:
#: England abolished fixed assured tenancies on 1 May 2026, so in the UK this is every
#: residential tenancy rather than an edge case. Scotland has been open-ended since 2017.
_OPEN_ENDED = {"GB", "DE", "NL", "AT", "SE", "DK", "NO", "FI", "CH"}

#: Which registry concept a country actually uses. The value is an i18n key; the client
#: translates it. Absent == fall back to the client's own translation files.
_REGISTRY_KEYS: dict[str, tuple[str | None, str | None]] = {
    # Israel: None on purpose. Falls through to property.block / property.plot, so the
    # Israeli rendering path is untouched code and Hebrew keeps working.
    "IL": (None, None),
    "US": ("property.registry.apn", None),
    "GB": ("property.registry.title_number", None),
    "IE": ("property.registry.folio_number", None),
    "AU": ("property.registry.lot_number", "property.registry.plan_number"),
    "NZ": ("property.registry.lot_number", "property.registry.plan_number"),
    "FR": ("property.registry.cadastral_ref", None),
    "ES": ("property.registry.cadastral_ref", None),
    "IT": ("property.registry.cadastral_ref", None),
    "PT": ("property.registry.cadastral_ref", None),
    "BE": ("property.registry.cadastral_ref", None),
    "LU": ("property.registry.cadastral_ref", None),
    "GR": ("property.registry.cadastral_ref", None),
    "NL": ("property.registry.cadastral_ref", None),
    "DE": ("property.registry.cadastral_ref", None),
    "AT": ("property.registry.cadastral_ref", None),
    "CH": ("property.registry.cadastral_ref", None),
}

#: E.164 country calling codes, for defaulting a phone number that was stored without one.
#:
#: NANP members all share ``1`` — the app only needs the dialling prefix, not the area code,
#: so that collision is harmless here.
_DIAL_CODES = {
    "AD":"376","AE":"971","AF":"93","AG":"1","AI":"1","AL":"355","AM":"374","AO":"244",
    "AQ":"672","AR":"54","AS":"1","AT":"43","AU":"61","AW":"297","AX":"358","AZ":"994",
    "BA":"387","BB":"1","BD":"880","BE":"32","BF":"226","BG":"359","BH":"973","BI":"257",
    "BJ":"229","BL":"590","BM":"1","BN":"673","BO":"591","BQ":"599","BR":"55","BS":"1",
    "BT":"975","BV":"47","BW":"267","BY":"375","BZ":"501","CA":"1","CC":"61","CD":"243",
    "CF":"236","CG":"242","CH":"41","CI":"225","CK":"682","CL":"56","CM":"237","CN":"86",
    "CO":"57","CR":"506","CU":"53","CV":"238","CW":"599","CX":"61","CY":"357","CZ":"420",
    "DE":"49","DJ":"253","DK":"45","DM":"1","DO":"1","DZ":"213","EC":"593","EE":"372",
    "EG":"20","EH":"212","ER":"291","ES":"34","ET":"251","FI":"358","FJ":"679","FK":"500",
    "FM":"691","FO":"298","FR":"33","GA":"241","GB":"44","GD":"1","GE":"995","GF":"594",
    "GG":"44","GH":"233","GI":"350","GL":"299","GM":"220","GN":"224","GP":"590","GQ":"240",
    "GR":"30","GS":"500","GT":"502","GU":"1","GW":"245","GY":"592","HK":"852","HM":"672",
    "HN":"504","HR":"385","HT":"509","HU":"36","ID":"62","IE":"353","IL":"972","IM":"44",
    "IN":"91","IO":"246","IQ":"964","IR":"98","IS":"354","IT":"39","JE":"44","JM":"1",
    "JO":"962","JP":"81","KE":"254","KG":"996","KH":"855","KI":"686","KM":"269","KN":"1",
    "KP":"850","KR":"82","KW":"965","KY":"1","KZ":"7","LA":"856","LB":"961","LC":"1",
    "LI":"423","LK":"94","LR":"231","LS":"266","LT":"370","LU":"352","LV":"371","LY":"218",
    "MA":"212","MC":"377","MD":"373","ME":"382","MF":"590","MG":"261","MH":"692","MK":"389",
    "ML":"223","MM":"95","MN":"976","MO":"853","MP":"1","MQ":"596","MR":"222","MS":"1",
    "MT":"356","MU":"230","MV":"960","MW":"265","MX":"52","MY":"60","MZ":"258","NA":"264",
    "NC":"687","NE":"227","NF":"672","NG":"234","NI":"505","NL":"31","NO":"47","NP":"977",
    "NR":"674","NU":"683","NZ":"64","OM":"968","PA":"507","PE":"51","PF":"689","PG":"675",
    "PH":"63","PK":"92","PL":"48","PM":"508","PN":"64","PR":"1","PS":"970","PT":"351",
    "PW":"680","PY":"595","QA":"974","RE":"262","RO":"40","RS":"381","RU":"7","RW":"250",
    "SA":"966","SB":"677","SC":"248","SD":"249","SE":"46","SG":"65","SH":"290","SI":"386",
    "SJ":"47","SK":"421","SL":"232","SM":"378","SN":"221","SO":"252","SR":"597","SS":"211",
    "ST":"239","SV":"503","SX":"1","SY":"963","SZ":"268","TC":"1","TD":"235","TF":"262",
    "TG":"228","TH":"66","TJ":"992","TK":"690","TL":"670","TM":"993","TN":"216","TO":"676",
    "TR":"90","TT":"1","TV":"688","TW":"886","TZ":"255","UA":"380","UG":"256","UM":"1",
    "US":"1","UY":"598","UZ":"998","VA":"39","VC":"1","VE":"58","VG":"1","VI":"1",
    "VN":"84","VU":"678","WF":"681","WS":"685","YE":"967","YT":"262","ZA":"27","ZM":"260",
    "ZW":"263",
}

def _build() -> dict[str, CountryConfig]:
    out: dict[str, CountryConfig] = {}
    for line in _ISO_TABLE.strip().splitlines():
        code, rest = line.split(" ", 1)
        if code in _EXCLUDED:
            continue
        name, currency = rest.rsplit(" ", 1)
        registry_1, registry_2 = _REGISTRY_KEYS.get(code, (None, None))
        out[code] = CountryConfig(
            country_code=code,
            name=name,
            open_ended_tenancies=code in _OPEN_ENDED,
            currency=currency,
            currency_symbol=currencies.symbol_for(currency),
            currency_symbol_position="suffix" if code in _SUFFIX_CURRENCY else "prefix",
            date_format="MDY" if code in _MDY else "YMD" if code in _YMD else "DMY",
            number_format=(
                "1.234,56" if code in _COMMA_DECIMAL
                else "1 234,56" if code in _SPACE_DECIMAL
                else "1,234.56"
            ),
            area_unit="sqft" if code in _SQFT else "sqm",
            revenue_basis_default="cash" if code in _CASH_BASIS else "accrual",
            has_postal_codes=code not in _NO_POSTAL_CODES,
            dial_code=_DIAL_CODES.get(code, ""),
            registry_key_1=registry_1,
            registry_key_2=registry_2,
        )

    # Israel is the only Tier N country, and the only one with any capability on. Its
    # formatting values are pinned to what the app already does today, so an Israeli
    # account renders byte-for-byte as before.
    out["IL"] = replace(
        out["IL"],
        tier="native",
        locale="he",
        capabilities=Capabilities(
            cpi_linkage=True,
            tax_tracks=True,
            israeli_property_types=True,
            bit_payments=True,
            structured_bank_details=True,
        ),
    )
    return out


COUNTRIES: dict[str, CountryConfig] = _build()

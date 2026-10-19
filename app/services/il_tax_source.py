"""Israel's government tax figures, for the tax tracks report.

**The booklet.** The Tax Authority publishes one "לוח עזר לחישוב מס הכנסה ממשכורת ושכר
עבודה" per year, as a PDF at a fixed address. It is the only machine-reachable official
source for the figures that change yearly — there is no API and no data.gov.il dataset — and
it is republished when the law changes mid-year (2026's was, in April, after the March
bracket change). Read from it:

- the monthly rent exemption ceiling (חוק הפטור, 1990)
- the brackets for personal-exertion income, and for other income (from 31%), which is what
  rent is under the marginal track
- the surtax threshold
- the value of one credit point

The text is matched line by line against patterns checked on every booklet 2022-2026, and
cross-checked (monthly × 12 = annual; the second table is the top of the first). Anything
that does not fit raises :class:`BookletError` and nothing is stored — a guessed bracket is
worse than none, and the report already says what is missing.

**The law.** Rates written in the law itself are not restated in the booklet in any form
worth parsing. They are below, each with the section it comes from and where that was
checked, and they change only when the law does — then this list is edited.
"""
import re
from dataclasses import dataclass

name = "il_booklet"

_BOOKLET_URL = (
    "https://www.gov.il/BlobFolder/generalpage/income-tax-monthly-deductions-booklet/he/"
    "generalInformation_income-tax-monthly-deductions-booklet_monthly-deductions-booklet-{year}.pdf"
)


class BookletError(ValueError):
    """The booklet's text did not read as expected; nothing from it is stored."""


@dataclass(frozen=True)
class LawValue:
    key: str
    value: float | int
    reference: str
    url: str
    first_year: int
    last_year: int | None = None


_ORDINANCE_RESEARCH = (
    "https://fs.knesset.gov.il/globaldocs/MMM/787f33ac-56d6-ee11-8162-005056aa4246/"
    "2_787f33ac-56d6-ee11-8162-005056aa4246_11_20517.pdf"
)

LAW_VALUES: tuple[LawValue, ...] = (
    LawValue(
        "ten_percent_rate_pct",
        10,
        "פקודת מס הכנסה, סעיף 122",
        _ORDINANCE_RESEARCH,
        first_year=2022,
    ),
    LawValue(
        "senior_age",
        60,
        "פקודת מס הכנסה, סעיף 121(א): מי שמלאו לו 60 בשנת המס, מדרגות המס חלות מ-10%",
        _ORDINANCE_RESEARCH,
        first_year=2022,
    ),
    LawValue(
        "surtax_rate_pct",
        3,
        "פקודת מס הכנסה, סעיף 121ב(א); הוראת ביצוע מס הכנסה 5/2025",
        "https://www.malam-payroll.com/wp-content/uploads/2025/04/IncomeTax_inst-05-2025.pdf",
        first_year=2022,
    ),
    LawValue(
        "capital_surtax_rate_pct",
        2,
        "פקודת מס הכנסה, סעיף 121ב(א1) (תיקון 276), על הכנסה ממקורות הוניים, "
        "לרבות דמי שכירות שאינם עסק; הוראת ביצוע מס הכנסה 5/2025",
        "https://www.malam-payroll.com/wp-content/uploads/2025/04/IncomeTax_inst-05-2025.pdf",
        first_year=2025,
    ),
    LawValue(
        "depreciation_rate_pct",
        2,
        'תקנות מס הכנסה (שיעור פחת לדירה מושכרת למגורים), התשמ"ט-1989, תקנה 2(א)',
        "https://www.nevo.co.il/law_html/law01/255_411.htm",
        first_year=2022,
    ),
    LawValue(
        # The default the report starts from when other income is too low to use up the
        # points, and the measure of "too low": the points a resident has before any for
        # children, a degree or being a woman.
        "resident_credit_points",
        2.25,
        "פקודת מס הכנסה, סעיף 34 (2 נקודות לתושב ישראל) ורבע נקודה בשל נסיעות",
        "https://www.kolzchut.org.il/he/נקודות_זיכוי_ממס_הכנסה_לתושב_ישראל",
        first_year=2022,
    ),
    LawValue(
        "rent_paid_deduction_cap",
        90_000,
        "פקודת מס הכנסה, סעיף 122(ו) (תיקון 264): ניכוי שכר דירה ששילם בעל דירה יחידה",
        "https://www.capitax.co.il/content/2/369",
        first_year=2023,
    ),
    LawValue(
        "passive_max_apartments",
        5,
        "טיוטת חוזר מקצועי של רשות המסים, פברואר 2018 (סיווג הכנסה מהשכרת דירות מגורים)",
        "https://www.capitax.co.il/content/1/1971",
        first_year=2022,
    ),
    LawValue(
        "business_min_apartments",
        10,
        "טיוטת חוזר מקצועי של רשות המסים, פברואר 2018 (סיווג הכנסה מהשכרת דירות מגורים)",
        "https://www.capitax.co.il/content/1/1971",
        first_year=2022,
    ),
)


def booklet_url(year: int) -> str:
    return _BOOKLET_URL.format(year=year)


def law_values(year: int) -> list[LawValue]:
    return [
        v
        for v in LAW_VALUES
        if v.first_year <= year and (v.last_year is None or year <= v.last_year)
    ]


def read_booklet(pdf: bytes) -> dict:
    """The figures in one year's booklet, or :class:`BookletError`."""
    text = _text_of(pdf)
    try:
        return parse_booklet_text(text)
    except BookletError as first:
        # PDFium builds differ on right-to-left text: some return each line in reading
        # order, some in display order, words reversed (5.14 and 5.13 on Windows did). The
        # booklet is the same file either way, so read it in the other order before giving up.
        try:
            return parse_booklet_text(_reading_order(text))
        except BookletError:
            raise first from None


def _text_of(pdf: bytes) -> str:
    import pypdfium2 as pdfium  # lazily, like document_extraction_service

    try:
        document = pdfium.PdfDocument(pdf)
    except pdfium.PdfiumError as exc:
        raise BookletError(f"not a readable PDF: {exc}") from exc
    try:
        return "\n".join(document[i].get_textpage().get_text_range() for i in range(len(document)))
    finally:
        document.close()


def _reading_order(text: str) -> str:
    """Undo display-order extraction: reverse each line's words, and put back the marks
    PDFium moved to the other side of a word ("-עד" → "עד-", "א33" → "33א", ",לפקודה" →
    "לפקודה,")."""
    def fix(word: str) -> str:
        if len(word) > 1 and word[0] in "-,." and _HEBREW.match(word[1]):
            word = word[1:] + word[0]
        return re.sub(r"^([א-ת]+)(\d+)$", r"\2\1", word)

    return "\n".join(" ".join(fix(w) for w in reversed(line.split())) for line in text.splitlines())


_HEBREW = re.compile(r"[א-ת]")

# One row of a bracket table: the monthly ceiling and rate, then the annual ones. The lower
# bound ("מ-6,451") is printed on every row but the first, sometimes without the hyphen.
_BRACKET_ROW = re.compile(
    r"(?:מ-? ?[\d,]+ )?עד-? ?([\d,]+) (\d+)% (?:מ-? ?[\d,]+ )?עד-? ?([\d,]+) (\d+)%"
)
_TOP_RATE = re.compile(r"מכל שקל נוסף (\d+)%")
_CEILING = re.compile(r"(?:תקרת הכנסה משכר דירה - |שכר דירה )פטור ממס ([\d,]+) לחודש")
_CREDIT_POINT = re.compile(r"נקודת זיכוי (?:33א )?([\d,]+) לחודש")
_SURTAX = re.compile(r"עלתה על ([\d,]+) ₪")


def parse_booklet_text(text: str) -> dict:
    # Extraction breaks lines mid-phrase ("מכל שקל\nנוסף\n 47%" in 2024 and 2025).
    flat = re.sub(r"\s+", " ", text)

    rows = []
    for monthly, monthly_rate, annual, annual_rate in _BRACKET_ROW.findall(flat):
        if monthly_rate != annual_rate or _int(monthly) * 12 != _int(annual):
            raise BookletError(f"bracket row does not add up: {monthly}/{annual}")
        rows.append((_int(annual), int(annual_rate)))

    # The personal-exertion table comes first, rates rising; the next table restarts lower.
    split = next((i for i in range(1, len(rows)) if rows[i][1] <= rows[i - 1][1]), len(rows))
    personal, other = rows[:split], rows[split:]
    if len(personal) < 2 or any(b[0] <= a[0] for a, b in zip(personal, personal[1:])):
        raise BookletError(f"personal brackets not found: {rows}")
    if not other or personal[-len(other):] != other:
        raise BookletError(f"other-income brackets are not the top of the personal ones: {rows}")

    top = _one(_TOP_RATE.findall(flat), "top rate")
    if int(top) <= personal[-1][1]:
        raise BookletError(f"top rate {top}% is not above the last bracket")

    def table(brackets):
        return [[upper, rate] for upper, rate in brackets] + [[None, int(top)]]

    return {
        "rent_exemption_ceiling_monthly": _int(_one(_CEILING.findall(flat), "exemption ceiling")),
        "personal_brackets": table(personal),
        "other_income_brackets": table(other),
        "surtax_threshold": _int(_one(_SURTAX.findall(flat), "surtax threshold")),
        "credit_point_monthly": _int(_one(_CREDIT_POINT.findall(flat), "credit point")),
    }


def _one(found: list[str], what: str) -> str:
    """The single value a pattern found — the same figure printed twice is fine, two
    different ones are not."""
    distinct = set(found)
    if len(distinct) != 1:
        raise BookletError(f"{what}: expected one value, found {sorted(distinct) or 'none'}")
    return found[0]


def _int(number: str) -> int:
    return int(number.replace(",", ""))

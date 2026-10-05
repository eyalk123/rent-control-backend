"""The property sheet: a one-page PDF an owner sends to a new renter.

**It is written for the renter, not the owner, so it is built from an allowlist.** A property
also holds what only the owner should see — the purchase price, the human owner's name, the
ownership document, revenue — and a field added to the model later must stay out of this file
until someone decides a renter should have it. Empty fields are left out rather than printed
as blanks, so a sparsely filled property still reads as a finished document.

Drawn with the reports' `_PDF`, which already carries the fonts and the right-to-left handling.
"""
import io
import logging
from dataclasses import dataclass

from PIL import Image

from app.models.property import Property
from app.schemas.property import _parse_parking_numbers
from app.services import country_service, firebase_storage, money_format
from app.services.report_service import FONT, _PDF, normalise_lang

logger = logging.getLogger(__name__)

# Preset illustrations are stored as this sentinel rather than as a file. A stock drawing on
# a document for a renter looks odd, so the sheet only ever shows a photo the owner uploaded.
PRESET_IMAGE_PREFIX = "rc-house:"

TEXT = {
    "en": {
        "title": "Property details",
        "section_property": "Property",
        "section_utilities": "Utilities",
        "section_payments": "Payments",
        "section_inventory": "Inventory notes",
        "type": "Type",
        "floor": "Floor",
        "unit": "Unit",
        "rooms": "Rooms",
        "area": "Surface area",
        "parking": "Parking",
        "electric_meter": "Electricity meter",
        "electric_account": "Electricity account",
        "water_meter": "Water meter",
        "water_account": "Water account",
        "property_tax": "Annual property tax",
        "building_fees": "Building / HOA fees",
        "per_month": "/mo",
        "block": "Block",
        "plot": "Plot",
    },
    "he": {
        "title": "פרטי הנכס",
        "section_property": "הנכס",
        "section_utilities": "חשמל ומים",
        "section_payments": "תשלומים",
        "section_inventory": "הערות דירה",
        "type": "סוג",
        "floor": "קומה",
        "unit": "דירה",
        "rooms": "חדרים",
        "area": "שטח",
        "parking": "חניה",
        "electric_meter": "מונה חשמל",
        "electric_account": "חשבון חשמל",
        "water_meter": "מונה מים",
        "water_account": "חשבון מים",
        "property_tax": "ארנונה שנתית",
        "building_fees": "ועד בית",
        "per_month": "/חודש",
        "block": "גוש",
        "plot": "חלקה",
    },
}

# Wording copied from the apps' locale files (`property.type_*`, `property.registry.*`) so the
# sheet says what the screen says.
TYPE_LABELS = {
    "en": {
        "apartment": "Apartment", "house": "House", "commercial": "Commercial",
        "garden_apartment": "Garden Apartment", "housing_unit": "Housing Unit",
        "condo_townhouse": "Condo / Townhouse", "penthouse": "Penthouse", "room": "Room",
        "other": "Other",
    },
    "he": {
        "apartment": "דירה", "house": "בית פרטי", "commercial": "מסחרי",
        "garden_apartment": "דירת גן", "housing_unit": "יחידת דיור",
        "condo_townhouse": "קונדו / בית טורי", "penthouse": "פנטהאוז", "room": "חדר",
        "other": "אחר",
    },
}

REGISTRY_LABELS = {
    "en": {
        "parcel_number": "Parcel number", "cadastral_ref": "Cadastral reference",
        "title_number": "Title number", "apn": "Parcel number (APN)",
        "folio_number": "Folio number", "lot_number": "Lot number", "plan_number": "Plan number",
    },
    "he": {
        "parcel_number": "מספר חלקה", "cadastral_ref": "מזהה קדסטראלי",
        "title_number": "מספר בעלות", "apn": "מספר חלקה (APN)",
        "folio_number": "מספר תיק", "lot_number": "מספר מגרש", "plan_number": "מספר תוכנית",
    },
}

AREA_UNITS = {"sqm": "m²", "sqft": "sq ft"}

# Layout, in mm.
PHOTO_MAX_H = 70
LABEL_W = 55
ROW_H = 6.5
LINE_H = 5.2
MUTED = (110, 116, 128)
RULE = (219, 222, 228)
INK = (20, 22, 26)


@dataclass
class SheetFormats:
    """How money and numbers are written: the account's, as on the reports."""

    currency: country_service.EffectiveCurrency
    number_format: str
    symbol_spaced: bool


def _registry_label(lang: str, key: str | None, fallback: str) -> str:
    if key is None:
        return TEXT[lang][fallback]
    return REGISTRY_LABELS[lang].get(key.rsplit(".", 1)[-1], TEXT[lang][fallback])


def _number(value: float, number_format: str) -> str:
    """3.5 rooms stays 3.5; 4.0 rooms prints as 4."""
    decimals = 0 if float(value).is_integer() else 1
    return money_format.group(value, number_format, decimals)


def sheet_rows(prop: Property, lang: str, formats: SheetFormats) -> list[tuple[str, list[tuple[str, str]]]]:
    """The sections and their (label, value) rows — only fields a renter should see, only
    when filled. A section with nothing in it is dropped."""
    lang = normalise_lang(lang)
    t = TEXT[lang]
    config = country_service.config_for(prop.country)
    parking = [p for p in (_parse_parking_numbers(prop.parking_numbers) or []) if p]

    def money(amount):
        return money_format.format_money(
            amount, formats.currency, formats.number_format, formats.symbol_spaced
        )

    type_value = prop.type.value if prop.type is not None else None
    property_rows = [
        (t["type"], TYPE_LABELS[lang].get(type_value, type_value) if type_value else None),
        (t["floor"], str(prop.floor) if prop.floor is not None else None),
        (t["unit"], prop.apartment),
        (t["rooms"], _number(prop.number_of_rooms, formats.number_format) if prop.number_of_rooms else None),
        (t["area"], f"{money_format.group(prop.sq_ft, formats.number_format)} {AREA_UNITS[config.area_unit]}" if prop.sq_ft else None),
        (t["parking"], ", ".join(parking) or None),
        (_registry_label(lang, config.registry_key_1, "block"), prop.block),
    ]
    # One identifier only where the country has one — Israel and Australia have two.
    if config.registry_key_1 is None or config.registry_key_2 is not None:
        property_rows.append((_registry_label(lang, config.registry_key_2, "plot"), prop.plot))

    sections = [
        (t["section_property"], property_rows),
        (t["section_utilities"], [
            (t["electric_meter"], prop.electricity_meter_number),
            (t["electric_account"], prop.electricity_account_number),
            (t["water_meter"], prop.water_meter_number),
            (t["water_account"], prop.water_account_number),
        ]),
        (t["section_payments"], [
            (t["property_tax"], money(prop.property_tax) if prop.property_tax else None),
            (t["building_fees"], f"{money(prop.house_committee)}{t['per_month']}" if prop.house_committee else None),
        ]),
    ]
    return [
        (title, filled)
        for title, rows in sections
        if (filled := [(label, str(value).strip()) for label, value in rows if value is not None and str(value).strip()])
    ]


def _photo(owner_id: str, image_url: str | None) -> io.BytesIO | None:
    """The uploaded photo as a JPEG, or None — a preset, a missing file or one Pillow cannot
    read all leave the sheet without a picture rather than failing it."""
    if not image_url or image_url.startswith(PRESET_IMAGE_PREFIX):
        return None
    raw = firebase_storage.download_owner_file(owner_id, image_url)
    if raw is None:
        return None
    try:
        image = Image.open(io.BytesIO(raw))
        image = image.convert("RGB")
        # Phone photos are 4000px wide; a 70mm-high header needs a fraction of that.
        image.thumbnail((1400, 1400))
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=85)
        out.seek(0)
        return out
    except Exception as exc:
        logger.warning("Property photo could not be read: %s", type(exc).__name__)
        return None


class _SheetPDF(_PDF):
    def header(self):
        pass

    def footer(self):
        pass

    def cell(self, *args, **kwargs):
        result = super().cell(*args, **kwargs)
        # fpdf2 2.8.9 remembers which font it last wrote to the page and skips writing it
        # again, but a Hebrew label is written through the fallback font without updating
        # that memory. The value cell beside it — digits, in Noto Sans — was then drawn in
        # Noto Sans Hebrew, which has no digits: every number came out as empty boxes.
        # Forgetting after each cell makes the next one name its font.
        self.current_font_is_set_on_page = False
        return result

    def wrap(self, text: str, width: float) -> list[str]:
        """Break `text` into lines that fit `width` mm at the current font.

        Done here rather than by `multi_cell`, because the bidi reordering must happen per
        *line*: reordering a whole Hebrew paragraph and then wrapping it puts its last words
        on the first line.
        """
        available = width - 2 * self.c_margin
        lines: list[str] = []
        for paragraph in text.splitlines() or [""]:
            line = ""
            for word in paragraph.split(" "):
                candidate = f"{line} {word}" if line else word
                if self.get_string_width(candidate) <= available:
                    line = candidate
                    continue
                if line:
                    lines.append(line)
                # A single word longer than the line (an account number, a URL) is cut.
                while self.get_string_width(word) > available and len(word) > 1:
                    cut = len(word)
                    while cut > 1 and self.get_string_width(word[:cut]) > available:
                        cut -= 1
                    lines.append(word[:cut])
                    word = word[cut:]
                line = word
            lines.append(line)
        return lines

    def text_lines(self, lines: list[str], x: float, width: float, height: float) -> None:
        align = "R" if self.rtl else "L"
        for line in lines:
            self.set_x(x)
            self.cell(width, height, line, align=align, new_x="LMARGIN", new_y="NEXT")

    def section_title(self, title: str) -> None:
        self.ensure_room(ROW_H * 3)
        self.ln(4)
        self.set_font(FONT, "B", 11)
        self.set_text_color(*INK)
        self.cell(0, 7, title, align="R" if self.rtl else "L", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*RULE)
        self.line(self.l_margin, self.get_y(), self.l_margin + self.epw, self.get_y())
        self.ln(1.5)

    def field(self, label: str, value: str) -> None:
        value_w = self.epw - LABEL_W
        self.set_font(FONT, "", 10)
        lines = self.wrap(value, value_w)
        self.ensure_room(max(ROW_H, LINE_H * len(lines)))
        top = self.get_y()
        label_x = self.l_margin + value_w if self.rtl else self.l_margin
        value_x = self.l_margin if self.rtl else self.l_margin + LABEL_W

        self.set_xy(label_x, top)
        self.set_text_color(*MUTED)
        self.cell(LABEL_W, ROW_H, self.fit(label, LABEL_W), align="R" if self.rtl else "L")

        self.set_xy(value_x, top)
        self.set_text_color(*INK)
        if len(lines) == 1:
            self.cell(value_w, ROW_H, lines[0], align="R" if self.rtl else "L",
                      new_x="LMARGIN", new_y="NEXT")
        else:
            self.set_y(top + (ROW_H - LINE_H) / 2)
            self.text_lines(lines, value_x, value_w, LINE_H)
            self.set_y(max(self.get_y(), top + ROW_H))


def generate_property_sheet_pdf(
    prop: Property,
    owner_id: str,
    lang: str,
    formats: SheetFormats,
) -> bytes:
    lang = normalise_lang(lang)
    pdf = _SheetPDF(None, None, lang=lang, orientation="P", unit="mm", format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    align = "R" if pdf.rtl else "L"

    photo = _photo(owner_id, prop.image_url)
    if photo is not None:
        with Image.open(photo) as probe:
            ratio = probe.width / probe.height
        photo.seek(0)
        width = min(pdf.epw, PHOTO_MAX_H * ratio)
        height = width / ratio
        pdf.image(photo, x=pdf.l_margin + (pdf.epw - width) / 2, y=pdf.get_y(), w=width, h=height)
        pdf.set_y(pdf.get_y() + height + 6)

    pdf.set_font(FONT, "", 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 5, TEXT[lang]["title"], align=align, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font(FONT, "B", 18)
    pdf.set_text_color(*INK)
    pdf.text_lines(pdf.wrap(prop.address, pdf.epw), pdf.l_margin, pdf.epw, 9)

    place = " ".join(part for part in (prop.city, prop.zip_code) if part)
    if place:
        pdf.set_font(FONT, "", 11)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 6, place, align=align, new_x="LMARGIN", new_y="NEXT")

    for title, rows in sheet_rows(prop, lang, formats):
        pdf.section_title(title)
        for label, value in rows:
            pdf.field(label, value)

    notes = (prop.inventory_notes or "").strip()
    if notes:
        pdf.section_title(TEXT[lang]["section_inventory"])
        pdf.set_font(FONT, "", 10)
        pdf.set_text_color(*INK)
        pdf.text_lines(pdf.wrap(notes, pdf.epw), pdf.l_margin, pdf.epw, LINE_H)

    return bytes(pdf.output())

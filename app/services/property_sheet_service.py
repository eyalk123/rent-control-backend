"""The property sheet: a one-page PDF an owner sends to a new renter.

**It is written for the renter, not the owner, so it is built from an allowlist.** A property
also holds what only the owner should see — the purchase price, the ownership document,
revenue — and a field added to the model later must stay out of this file until someone
decides a renter should have it. The property owner's contact details (name, phone, email,
bank account, from ``owner_record``) are on it deliberately, so the tenant knows who to pay
and where; the owner record's notes are not. Empty fields are left out rather than printed
as blanks, so a sparsely filled property still reads as a finished document.

Drawn with the reports' `_PDF`, which already carries the fonts and the right-to-left handling.
"""
import io
import logging
from dataclasses import dataclass
from datetime import date

from PIL import Image

from app.models.property import Property
from app.schemas.property import _parse_parking_numbers
from app.services import country_service, firebase_storage, money_format
from app.services.report_service import FONT, MONTH_NAMES_BY_LANG, _PDF, normalise_lang

logger = logging.getLogger(__name__)

# Preset illustrations are stored as this sentinel rather than as a file. A stock drawing on
# a document for a renter looks odd, so the sheet only ever shows a photo the owner uploaded.
PRESET_IMAGE_PREFIX = "rc-house:"

# Property tax and building fees carry no period: the form asks for an amount and nothing
# else, owners enter whatever their bill says (Israeli arnona is usually bi-monthly), and the
# sheet must not restate it as annual or monthly on their behalf.
TEXT = {
    "en": {
        "kicker": "PROPERTY DETAILS",
        "section_property": "Property",
        "section_utilities": "Utilities",
        "section_payments": "Payments",
        "section_inventory": "Inventory notes",
        "section_owner": "Property owner",
        "owner_name": "Name",
        "owner_phone": "Phone",
        "owner_email": "Email",
        "owner_bank": "Bank account",
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
        "property_tax": "Property tax",
        "building_fees": "Building / HOA fees",
        "block": "Block",
        "plot": "Plot",
        "prepared": "Prepared {date}",
    },
    "he": {
        "kicker": "פרטי הנכס",
        "section_property": "הנכס",
        "section_utilities": "חשמל ומים",
        "section_payments": "תשלומים",
        "section_inventory": "הערות דירה",
        "section_owner": "בעל הנכס",
        "owner_name": "שם",
        "owner_phone": "טלפון",
        "owner_email": "אימייל",
        "owner_bank": "חשבון בנק",
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
        "property_tax": "ארנונה",
        "building_fees": "ועד בית",
        "block": "גוש",
        "plot": "חלקה",
        "prepared": "הופק ב-{date}",
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
MARGIN = 16
BAND_H = 46
PHOTO_W = 54
TILE_H = 19
TILE_GAP = 3
ROW_H = 7.5
LINE_H = 5.2
CARD_PAD = 4

# The mobile app's brand navy, with the tints the apps put around it.
NAVY = (30, 58, 95)
NAVY_SOFT = (164, 186, 214)
INK = (20, 26, 36)
MUTED = (104, 112, 126)
TILE_FILL = (238, 242, 248)
CARD_FILL = (249, 250, 252)
RULE = (226, 230, 236)


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


def _filled(rows: list[tuple[str, object]]) -> list[tuple[str, str]]:
    return [(label, str(value).strip()) for label, value in rows if value is not None and str(value).strip()]


def sheet_facts(prop: Property, lang: str, formats: SheetFormats) -> list[tuple[str, str]]:
    """The headline figures shown as tiles under the header, filled ones only."""
    lang = normalise_lang(lang)
    t = TEXT[lang]
    config = country_service.config_for(prop.country)
    type_value = prop.type.value if prop.type is not None else None
    return _filled([
        (t["type"], TYPE_LABELS[lang].get(type_value, type_value) if type_value else None),
        (t["rooms"], _number(prop.number_of_rooms, formats.number_format) if prop.number_of_rooms else None),
        (t["area"], f"{money_format.group(prop.sq_ft, formats.number_format)} {AREA_UNITS[config.area_unit]}" if prop.sq_ft else None),
        # Floor 0 is a ground floor, not an empty field.
        (t["floor"], str(prop.floor) if prop.floor is not None else None),
        (t["unit"], prop.apartment),
    ])


def sheet_rows(prop: Property, lang: str, formats: SheetFormats) -> list[tuple[str, list[tuple[str, str]]]]:
    """The sections below the tiles and their (label, value) rows — only fields a renter
    should see, only when filled. A section with nothing in it is dropped."""
    lang = normalise_lang(lang)
    t = TEXT[lang]
    config = country_service.config_for(prop.country)
    parking = [p for p in (_parse_parking_numbers(prop.parking_numbers) or []) if p]

    def money(amount):
        return money_format.format_money(
            amount, formats.currency, formats.number_format, formats.symbol_spaced
        )

    property_rows = [
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
            (t["building_fees"], money(prop.house_committee) if prop.house_committee else None),
        ]),
    ]
    # Next to Payments: who to pay, and where.
    owner = getattr(prop, "owner_record", None)
    if owner is not None:
        sections.append((t["section_owner"], [
            (t["owner_name"], owner.name),
            (t["owner_phone"], owner.phone),
            (t["owner_email"], owner.email),
            (t["owner_bank"], owner.bank_account),
        ]))
    return [(title, filled) for title, rows in sections if (filled := _filled(rows))]


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
        # Cropped to the header slot's shape, so a portrait phone photo fills it rather than
        # sitting in it as a sliver.
        target = PHOTO_W / (BAND_H - 2 * 6)
        ratio = image.width / image.height
        if ratio > target:
            width = int(image.height * target)
            left = (image.width - width) // 2
            image = image.crop((left, 0, left + width, image.height))
        else:
            height = int(image.width / target)
            top = (image.height - height) // 2
            image = image.crop((0, top, image.width, top + height))
        image.thumbnail((900, 900))
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
        self.set_y(-12)
        self.set_font(FONT, "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, getattr(self, "footer_text", ""), align="C")

    def cell(self, *args, **kwargs):
        result = super().cell(*args, **kwargs)
        # fpdf2 2.8.9 remembers which font it last wrote to the page and skips writing it
        # again, but a Hebrew label is written through the fallback font without updating
        # that memory. The value cell beside it — digits, in Noto Sans — was then drawn in
        # Noto Sans Hebrew, which has no digits: every number came out as empty boxes.
        # Forgetting after each cell makes the next one name its font.
        self.current_font_is_set_on_page = False
        return result

    @property
    def align(self) -> str:
        return "R" if self.rtl else "L"

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
        for line in lines:
            self.set_x(x)
            self.cell(width, height, line, align=self.align, new_x="LMARGIN", new_y="NEXT")

    def band(self, address: str, place: str, kicker: str, photo: io.BytesIO | None) -> None:
        """The navy header across the full width of the page."""
        self.set_fill_color(*NAVY)
        self.rect(0, 0, self.w, BAND_H, style="F")

        text_w = self.epw - (PHOTO_W + 6 if photo else 0)
        text_x = self.l_margin + (PHOTO_W + 6 if photo and self.rtl else 0)
        if photo is not None:
            photo_x = self.l_margin if self.rtl else self.l_margin + self.epw - PHOTO_W
            self.image(photo, x=photo_x, y=6, w=PHOTO_W, h=BAND_H - 12)

        self.set_font(FONT, "B", 22)
        address_lines = self.wrap(address, text_w)[:2]
        block_h = 5 + 2 + 10 * len(address_lines) + (6 if place else 0)
        self.set_y((BAND_H - block_h) / 2)

        self.set_x(text_x)
        self.set_font(FONT, "B", 8)
        self.set_text_color(*NAVY_SOFT)
        # Letter-spaced in English only: Hebrew has no capitals to space out.
        self.set_char_spacing(0 if self.rtl else 0.8)
        self.cell(text_w, 5, kicker, align=self.align, new_x="LMARGIN", new_y="NEXT")
        self.set_char_spacing(0)
        self.ln(2)

        self.set_font(FONT, "B", 22)
        self.set_text_color(255, 255, 255)
        self.text_lines(address_lines, text_x, text_w, 10)
        if place:
            self.set_font(FONT, "", 11)
            self.set_text_color(*NAVY_SOFT)
            self.set_x(text_x)
            self.cell(text_w, 6, place, align=self.align, new_x="LMARGIN", new_y="NEXT")
        self.set_y(BAND_H + 8)

    def tiles(self, facts: list[tuple[str, str]]) -> None:
        if not facts:
            return
        count = len(facts)
        width = (self.epw - TILE_GAP * (count - 1)) / count
        top = self.get_y()
        # Logical order runs from the reading edge, so the first fact sits on the right in Hebrew.
        for index, (label, value) in enumerate(facts):
            slot = count - 1 - index if self.rtl else index
            x = self.l_margin + slot * (width + TILE_GAP)
            self.set_fill_color(*TILE_FILL)
            self.rect(x, top, width, TILE_H, style="F", round_corners=True, corner_radius=2)
            self.set_xy(x, top + 3)
            self.set_font(FONT, "B", 14 if count <= 4 else 12)
            self.set_text_color(*NAVY)
            self.cell(width, 7, self.fit(value, width), align="C")
            self.set_xy(x, top + 11)
            self.set_font(FONT, "", 8)
            self.set_text_color(*MUTED)
            self.cell(width, 5, self.fit(label, width), align="C")
        self.set_y(top + TILE_H + 7)

    def section_title(self, title: str) -> None:
        self.ensure_room(ROW_H * 3)
        top = self.get_y()
        accent_x = self.l_margin + self.epw - 1.2 if self.rtl else self.l_margin
        self.set_fill_color(*NAVY)
        self.rect(accent_x, top + 1.2, 1.2, 5, style="F")
        self.set_font(FONT, "B", 11)
        self.set_text_color(*INK)
        self.set_x(self.l_margin + (0 if self.rtl else 3))
        self.cell(self.epw - 3, 7.5, title, align=self.align, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def field_lines(self, value: str, label_w: float) -> tuple[list[str], float]:
        """The value's wrapped lines and the row height they need."""
        self.set_font(FONT, "", 10)
        lines = self.wrap(value, self.epw - label_w - 2 * CARD_PAD)
        return lines, max(ROW_H, LINE_H * len(lines) + 2)

    def field(self, label: str, value: str, label_w: float, last: bool) -> None:
        value_w = self.epw - label_w - 2 * CARD_PAD
        lines, height = self.field_lines(value, label_w)
        top = self.get_y()
        inner_l = self.l_margin + CARD_PAD
        label_x = inner_l + value_w if self.rtl else inner_l
        value_x = inner_l if self.rtl else inner_l + label_w

        self.set_xy(label_x, top)
        self.set_text_color(*MUTED)
        self.cell(label_w, ROW_H, self.fit(label, label_w), align=self.align)

        self.set_text_color(*INK)
        self.set_y(top + (ROW_H - LINE_H * len(lines)) / 2 if len(lines) == 1 else top + 1)
        self.text_lines(lines, value_x, value_w, LINE_H)
        self.set_y(top + height)
        if not last:
            self.set_draw_color(*RULE)
            self.set_line_width(0.2)
            self.line(inner_l, self.get_y(), inner_l + self.epw - 2 * CARD_PAD, self.get_y())

    def card(self, rows: list[tuple[str, str]]) -> None:
        """A tinted block holding one section's rows."""
        self.set_font(FONT, "", 10)
        label_w = min(60, max(self.get_string_width(label) for label, _ in rows) + 8)
        # The box is drawn before the rows, so its height is worked out first. A section is
        # kept whole on one page — at most a handful of short rows.
        height = sum(self.field_lines(value, label_w)[1] for _, value in rows)
        self.ensure_room(height)
        top = self.get_y()
        self.set_fill_color(*CARD_FILL)
        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.rect(self.l_margin, top, self.epw, height, style="DF", round_corners=True, corner_radius=2)
        for i, (label, value) in enumerate(rows):
            self.field(label, value, label_w, i == len(rows) - 1)
        self.ln(7)


def _prepared_line(lang: str, today: date) -> str:
    when = f"{today.day} {MONTH_NAMES_BY_LANG[lang][today.month - 1]} {today.year}"
    return f"{TEXT[lang]['prepared'].format(date=when)}  ·  RentVance"


def generate_property_sheet_pdf(
    prop: Property,
    owner_id: str,
    lang: str,
    formats: SheetFormats,
    today: date | None = None,
) -> bytes:
    lang = normalise_lang(lang)
    pdf = _SheetPDF(None, None, lang=lang, orientation="P", unit="mm", format="A4")
    pdf.footer_text = _prepared_line(lang, today or date.today())
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    place = " ".join(part for part in (prop.city, prop.zip_code) if part)
    pdf.band(prop.address, place, TEXT[lang]["kicker"], _photo(owner_id, prop.image_url))
    pdf.tiles(sheet_facts(prop, lang, formats))

    for title, rows in sheet_rows(prop, lang, formats):
        pdf.section_title(title)
        pdf.card(rows)

    notes = (prop.inventory_notes or "").strip()
    if notes:
        pdf.section_title(TEXT[lang]["section_inventory"])
        pdf.set_font(FONT, "", 10)
        lines = pdf.wrap(notes, pdf.epw - 2 * CARD_PAD)
        pdf.ensure_room(LINE_H * min(len(lines), 4) + 2 * CARD_PAD)
        top = pdf.get_y()
        height = LINE_H * len(lines) + 2 * CARD_PAD
        # A note longer than the page left is drawn without its box rather than with one
        # that runs off the page.
        if top + height <= pdf.page_break_trigger:
            pdf.set_fill_color(*CARD_FILL)
            pdf.set_draw_color(*RULE)
            pdf.rect(pdf.l_margin, top, pdf.epw, height, style="DF", round_corners=True, corner_radius=2)
        pdf.set_y(top + CARD_PAD)
        pdf.set_text_color(*INK)
        pdf.text_lines(lines, pdf.l_margin + CARD_PAD, pdf.epw - 2 * CARD_PAD, LINE_H)

    return bytes(pdf.output())

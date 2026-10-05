"""The property sheet — a PDF an owner sends to a new renter.

What matters most is what it leaves out: it is built from an allowlist, so the owner's own
figures and the human owner's name never reach a renter.
"""
import ctypes
import io

import pypdfium2
import pypdfium2.raw as pdfium_c

from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_property
from app.models.property import PropertyTypeEnum
from app.services import country_service
from app.services.property_sheet_service import SheetFormats, sheet_rows


def _text(pdf_bytes: bytes) -> str:
    doc = pypdfium2.PdfDocument(io.BytesIO(pdf_bytes))
    return "\n".join(page.get_textpage().get_text_range() for page in doc)


def _digit_fonts(pdf_bytes: bytes) -> set[str]:
    """The font every digit on the first page is drawn in."""
    textpage = pypdfium2.PdfDocument(io.BytesIO(pdf_bytes))[0].get_textpage()
    text = textpage.get_text_range()
    fonts = set()
    buffer = ctypes.create_string_buffer(256)
    for index, char in enumerate(text):
        if char.isdigit():
            pdfium_c.FPDFText_GetFontInfo(textpage.raw, index, buffer, 256, None)
            fonts.add(buffer.value.decode())
    return fonts


def _formats(country=None):
    config = country_service.config_for(country)
    return SheetFormats(
        country_service.effective_currency(country), config.number_format,
        config.currency_symbol_spaced,
    )


def _full_property(db_session, **kw):
    fields = dict(
        address="10 Rothschild Blvd",
        city="Tel Aviv",
        zip_code="61000",
        floor=3,
        apartment="7",
        number_of_rooms=3.5,
        sq_ft=95,
        parking_numbers='["A12"]',
        electricity_meter_number="EM-555",
        water_account_number="WA-777",
        property_tax=4800,
        house_committee=250,
        inventory_notes="Fridge, oven\nTwo air conditioners",
        purchase_price=2_500_000,
        property_owner="Secret Owner",
    )
    return make_property(db_session, **{**fields, **kw})


def test_sheet_downloads_as_pdf(client, db_session):
    prop = _full_property(db_session)
    resp = client.get(f"/properties/{prop.id}/sheet")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    text = _text(resp.content)
    for expected in ("10 Rothschild Blvd", "EM-555", "WA-777", "A12", "Fridge, oven"):
        assert expected in text


def test_sheet_leaves_out_owner_only_fields(client, db_session):
    prop = _full_property(db_session)
    text = _text(client.get(f"/properties/{prop.id}/sheet").content)
    assert "Secret Owner" not in text
    assert "2,500,000" not in text


def test_sheet_renders_in_hebrew(client, db_session):
    prop = _full_property(
        db_session, address="רחוב הרצל 12", city="תל אביב",
        inventory_notes="מקרר, תנור ושני מזגנים " * 12,
    )
    resp = client.get(f"/properties/{prop.id}/sheet", params={"lang": "he"})
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")


def test_hebrew_sheet_draws_numbers_in_a_font_that_has_digits(client, db_session):
    """The production demo property, field for field.

    On fpdf2 2.8.9 the values after a Hebrew cell were drawn in the font it left behind —
    the Hebrew one, which has no digits — so every number printed as empty boxes.
    """
    prop = make_property(
        db_session, address="ז'בוטינסקי 33", city="רמת גן", zip_code=None, floor=6,
        apartment="12", number_of_rooms=4.0, sq_ft=98, parking_numbers='["31"]',
        block="6152", plot="77", property_tax=690.0, house_committee=320.0,
    )
    resp = client.get(f"/properties/{prop.id}/sheet", params={"lang": "he"})
    fonts = _digit_fonts(resp.content)
    assert fonts and not any("Hebrew" in font for font in fonts), fonts


def test_sheet_is_owner_scoped(client_factory, db_session):
    prop = make_property(db_session, owner_id=OWNER_A)
    assert client_factory(OWNER_B).get(f"/properties/{prop.id}/sheet").status_code == 404


def test_empty_fields_and_sections_are_dropped(db_session):
    prop = make_property(db_session, type=PropertyTypeEnum.APARTMENT, floor=0)
    sections = dict(sheet_rows(prop, "en", _formats()))
    # Floor 0 is a ground floor, not an empty field.
    assert dict(sections["Property"])["Floor"] == "0"
    assert "Utilities" not in sections
    assert "Payments" not in sections


def test_registry_label_follows_the_country(db_session):
    prop = make_property(db_session, country="GB", block="NGL123456", plot="ignored")
    rows = dict(dict(sheet_rows(prop, "en", _formats("GB")))["Property"])
    assert rows["Title number"] == "NGL123456"
    # The UK has one identifier, so the second box never prints.
    assert "ignored" not in rows.values()

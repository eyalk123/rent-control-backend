"""Unit tests for receipt scanning: the clean-up, the image handling, and the model call."""
import base64
import io
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.schemas.document_extraction import ReceiptExtraction, ReceiptFieldNote
from app.services.document_extraction_service import DocumentExtractionService
from app.services.receipt_extraction import (
    RECEIPT_TOOL_NAME,
    CategoryOption,
    PropertyOption,
    ReceiptCatalog,
    SupplierOption,
    catalog_text,
    clean_receipt,
    normalize_receipt_image,
)

ELECTRICITY, PLUMBING, GARDENING = 1, 2, 3

CATALOG = ReceiptCatalog(
    categories=(
        CategoryOption(ELECTRICITY, "electricity"),
        CategoryOption(PLUMBING, "repairs"),
        CategoryOption(GARDENING, "gardening"),
    ),
    suppliers=(
        SupplierOption(10, "יוסי אינסטלציה", phone="050-1234567", category_ids=(PLUMBING,)),
        SupplierOption(11, "Green & Co", category_ids=(GARDENING, PLUMBING)),
    ),
    properties=(PropertyOption(100, "Herzl 12, apt 3, Tel Aviv"),),
)


def _clean(country="IL", catalog=CATALOG, **fields) -> ReceiptExtraction:
    return clean_receipt(ReceiptExtraction(**fields), catalog, country)


# --- ids must come from the owner's catalog ---

def test_ids_not_in_the_catalog_become_empty_fields():
    out = _clean(category_ids=[PLUMBING, 999], supplier_id=999, property_id=999)
    assert out.category_ids == [PLUMBING]
    assert out.supplier_id is None
    assert out.property_id is None


def test_duplicate_category_ids_are_collapsed():
    assert _clean(category_ids=[PLUMBING, PLUMBING]).category_ids == [PLUMBING]


def test_a_supplier_outside_the_chosen_category_is_kept_with_the_category():
    """Supplier and category are independent fields: a mismatch is the user's call, and the
    form warns about it before saving."""
    out = _clean(category_ids=[ELECTRICITY], supplier_id=10, supplier_name="יוסי")
    assert out.category_ids == [ELECTRICITY]
    assert out.supplier_id == 10


def test_a_single_category_supplier_fills_an_empty_category():
    out = _clean(supplier_id=10)
    assert out.supplier_id == 10
    assert out.category_ids == [PLUMBING]


def test_a_multi_category_supplier_with_no_category_keeps_the_category_empty():
    """Choosing one of their categories would be a guess, so the user picks it."""
    out = _clean(supplier_id=11)
    assert out.supplier_id == 11
    assert out.category_ids == []


def test_property_is_dropped_when_no_property_list_was_given():
    catalog = ReceiptCatalog(categories=CATALOG.categories, suppliers=CATALOG.suppliers)
    assert _clean(catalog=catalog, property_id=100).property_id is None
    assert _clean(property_id=100).property_id == 100


# --- plain values ---

def test_non_positive_amount_and_unparseable_date_are_dropped():
    out = _clean(amount=0, date="31/02/2026")
    assert out.amount is None
    assert out.date is None


def test_a_future_date_is_kept_and_flagged():
    future = (date.today() + timedelta(days=30)).isoformat()
    out = _clean(date=future)
    assert out.date == future
    assert [(n.field, n.confidence) for n in out.notes] == [("date", "low")]


def test_payment_method_aliases_map_and_unknown_values_drop():
    assert _clean(payment_method="credit_card").payment_method == "card"
    assert _clean(payment_method="barter").payment_method is None


def test_israeli_payment_apps_only_where_the_country_has_them():
    assert _clean(payment_method="bit").payment_method == "bit"
    assert _clean(country="US", payment_method="bit").payment_method is None
    assert _clean(country="US", payment_method="cash").payment_method == "cash"


def test_notes_about_empty_fields_are_pruned():
    out = _clean(
        amount=450,
        supplier_id=999,
        notes=[
            ReceiptFieldNote(field="amount", confidence="low", source_text="45O"),
            ReceiptFieldNote(field="supplier_id", confidence="medium", source_text="יוסי"),
            ReceiptFieldNote(field="nonsense", confidence="low"),
        ],
    )
    assert [n.field for n in out.notes] == ["amount"]


def test_catalog_text_says_when_there_is_nothing_to_pick():
    text = catalog_text(ReceiptCatalog())
    assert "category_ids is always empty" in text
    assert "supplier_id is always null" in text
    assert "property_id is always null" in text


# --- image handling ---

def _jpeg(size, orientation=None) -> bytes:
    from PIL import Image

    img = Image.new("RGB", size, "white")
    out = io.BytesIO()
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        img.save(out, format="JPEG", exif=exif)
    else:
        img.save(out, format="JPEG")
    return out.getvalue()


def test_a_sideways_phone_photo_is_turned_upright_and_scaled_down():
    from PIL import Image

    # Stored as 4000x3000 landscape pixels with "rotate 90°" in the EXIF — a portrait shot.
    out = Image.open(io.BytesIO(normalize_receipt_image(_jpeg((4000, 3000), orientation=6))))
    assert out.size == (1500, 2000)


def test_unreadable_image_bytes_raise_422():
    with pytest.raises(HTTPException) as exc:
        normalize_receipt_image(b"not an image")
    assert exc.value.status_code == 422


# --- the model call ---

class _FakeUsage:
    input_tokens = 900
    output_tokens = 120
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _FakeToolUse:
    type = "tool_use"
    name = RECEIPT_TOOL_NAME

    def __init__(self, tool_input):
        self.input = tool_input


class _FakeResponse:
    stop_reason = "tool_use"
    usage = _FakeUsage()

    def __init__(self, tool_input):
        self.content = [_FakeToolUse(tool_input)]


class _FakeClient:
    def __init__(self, tool_input):
        self.calls = []
        response = _FakeResponse(tool_input)

        class _Messages:
            def create(inner, **kwargs):
                self.calls.append(kwargs)
                return response

        self.messages = _Messages()


def test_extract_receipt_sends_the_catalog_and_returns_a_cleaned_draft(monkeypatch):
    svc = DocumentExtractionService(api_key="k", model="claude-sonnet-4-6")
    fake = _FakeClient({"amount": 450, "supplier_id": 10, "category_ids": [], "property_id": 999})
    monkeypatch.setattr(svc, "_client", lambda: fake)

    result = svc.extract_receipt(_jpeg((800, 600)), "image/png", CATALOG, "IL")

    assert result.extraction.amount == 450
    assert result.extraction.supplier_id == 10
    assert result.extraction.category_ids == [PLUMBING]  # filled from the supplier
    assert result.extraction.property_id is None  # 999 was never on the list
    assert result.meta.fields_extracted == 3

    call = fake.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": RECEIPT_TOOL_NAME}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    content = call["messages"][0]["content"]
    assert "10: יוסי אינסטלציה" in content[0]["text"]
    # Every photo is re-encoded upright as JPEG, whatever it arrived as.
    assert content[1]["source"]["media_type"] == "image/jpeg"
    assert base64.standard_b64decode(content[1]["source"]["data"]).startswith(b"\xff\xd8")


def test_extract_receipt_rejects_a_word_document():
    svc = DocumentExtractionService(api_key="k", model="claude-sonnet-4-6")
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with pytest.raises(HTTPException) as exc:
        svc.extract_receipt(b"PK", docx, CATALOG)
    assert exc.value.status_code == 415

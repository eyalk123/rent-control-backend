"""Unit tests for DocumentExtractionService (Anthropic client mocked)."""
import base64
import io

import pytest
from fastapi import HTTPException

from app.schemas.document_extraction import FieldNote, LeaseExtraction, LeaseYearGuess
from app.services.document_extraction_service import (
    DocumentExtractionService,
    _clean_extraction,
)

PDF_MEDIA = "application/pdf"
DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _service(api_key="test-key"):
    return DocumentExtractionService(api_key=api_key, model="claude-opus-4-8")


class _FakeMessages:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.messages = _FakeMessages(response)


class _FakeUsage:
    input_tokens = 1000
    output_tokens = 500
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _FakeToolUse:
    type = "tool_use"
    name = "record_lease_extraction"

    def __init__(self, tool_input):
        self.input = tool_input


class _FakeResponse:
    def __init__(self, content, stop_reason="tool_use"):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _FakeUsage()


# --- content block construction (per file type) ---

def test_pdf_builds_document_block():
    block = _service()._build_content_block(b"%PDF-1.4 data", PDF_MEDIA)
    assert block["type"] == "document"
    assert block["source"]["media_type"] == PDF_MEDIA
    assert base64.standard_b64decode(block["source"]["data"]) == b"%PDF-1.4 data"


@pytest.mark.parametrize("media", ["image/png", "image/jpeg", "image/webp", "image/gif"])
def test_image_builds_image_block(media):
    block = _service()._build_content_block(b"\x89PNG bytes", media)
    assert block["type"] == "image"
    assert block["source"]["media_type"] == media
    assert base64.standard_b64decode(block["source"]["data"]) == b"\x89PNG bytes"


def test_content_type_with_charset_suffix_is_handled():
    block = _service()._build_content_block(b"%PDF", "application/pdf; charset=binary")
    assert block["type"] == "document"


def test_docx_builds_text_block_from_paragraphs_and_tables():
    from docx import Document

    doc = Document()
    doc.add_paragraph("Lease for 12 Herzl St, Tel Aviv")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Rent"
    table.rows[0].cells[1].text = "5000"
    buf = io.BytesIO()
    doc.save(buf)

    block = _service()._build_content_block(buf.getvalue(), DOCX_MEDIA)
    assert block["type"] == "text"
    assert "12 Herzl St" in block["text"]
    assert "Rent | 5000" in block["text"]


def test_unsupported_type_raises_415():
    with pytest.raises(HTTPException) as exc:
        _service()._build_content_block(b"x", "text/csv")
    assert exc.value.status_code == 415


# --- extract_lease orchestration ---

def test_extract_lease_returns_result_with_meta(monkeypatch):
    svc = _service()
    draft = LeaseExtraction()
    draft.property.city = "Tel Aviv"
    draft.renter.base_rent = 5000.0
    draft.notes = [FieldNote(section="renter", field="base_rent", confidence="medium", source_text="5,000 ₪")]
    fake = _FakeClient(_FakeResponse([_FakeToolUse(draft.model_dump())]))
    monkeypatch.setattr(svc, "_client", lambda: fake)

    result = svc.extract_lease(b"%PDF data", PDF_MEDIA)

    assert result.extraction.property.city == "Tel Aviv"
    assert result.extraction.renter.base_rent == 5000.0
    # Telemetry computed from the response + parsed tool input.
    assert result.meta.input_tokens == 1000
    assert result.meta.output_tokens == 500
    assert result.meta.fields_extracted == 2  # city + base_rent populated
    assert result.meta.medium_confidence_count == 1  # from the note
    assert result.meta.estimated_cost_usd is not None
    # A non-strict extraction tool was forced, and the PDF content block was sent.
    call = fake.messages.calls[0]
    assert call["tool_choice"]["name"] == "record_lease_extraction"
    assert call["messages"][0]["content"][0]["type"] == "document"


def test_extract_lease_refusal_raises_422(monkeypatch):
    svc = _service()
    fake = _FakeClient(_FakeResponse([], stop_reason="refusal"))
    monkeypatch.setattr(svc, "_client", lambda: fake)
    with pytest.raises(HTTPException) as exc:
        svc.extract_lease(b"%PDF", PDF_MEDIA)
    assert exc.value.status_code == 422


def test_extract_lease_no_tool_call_raises_422(monkeypatch):
    svc = _service()
    fake = _FakeClient(_FakeResponse([], stop_reason="max_tokens"))
    monkeypatch.setattr(svc, "_client", lambda: fake)
    with pytest.raises(HTTPException) as exc:
        svc.extract_lease(b"%PDF", PDF_MEDIA)
    assert exc.value.status_code == 422


def test_missing_api_key_raises_503():
    with pytest.raises(HTTPException) as exc:
        _service(api_key="").extract_lease(b"%PDF", PDF_MEDIA)
    assert exc.value.status_code == 503


# --- validation cleaning (don't hand the user values that would error on submit) ---

def test_clean_drops_out_of_range_payment_day():
    e = LeaseExtraction()
    e.renter.payment_day_of_month = 45
    _clean_extraction(e)
    assert e.renter.payment_day_of_month is None


def test_clean_keeps_valid_payment_day():
    e = LeaseExtraction()
    e.renter.payment_day_of_month = 10
    _clean_extraction(e)
    assert e.renter.payment_day_of_month == 10


def test_clean_drops_non_iso_lease_start():
    e = LeaseExtraction()
    e.renter.lease_start = "see addendum"
    _clean_extraction(e)
    assert e.renter.lease_start is None


def test_clean_keeps_iso_lease_start():
    e = LeaseExtraction()
    e.renter.lease_start = "2025-03-01"
    _clean_extraction(e)
    assert e.renter.lease_start == "2025-03-01"


def test_clean_drops_negative_amounts():
    e = LeaseExtraction()
    e.renter.base_rent = -500.0
    e.property.sq_ft = -80
    e.renter.lease_years = [LeaseYearGuess(amount=-1, type="contract")]
    _clean_extraction(e)
    assert e.renter.base_rent is None
    assert e.property.sq_ft is None
    assert e.renter.lease_years is None


def test_clean_drops_note_for_nulled_field():
    e = LeaseExtraction()
    e.renter.payment_day_of_month = 45  # invalid → will be nulled
    e.renter.base_rent = 5000.0
    e.notes = [
        FieldNote(section="renter", field="payment_day_of_month", confidence="low", source_text="?"),
        FieldNote(section="renter", field="base_rent", confidence="medium", source_text="5,000"),
    ]
    _clean_extraction(e)
    # The note for the nulled field is removed; the one for a surviving field stays.
    assert [n.field for n in e.notes] == ["base_rent"]

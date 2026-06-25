"""Lease document extraction via Claude (vision + structured output).

The service is the only place that talks to the Anthropic API. It receives the
raw upload bytes, builds the right content block for the file type (PDF and images
go to Claude directly; DOCX is converted to text with python-docx), and asks Claude
to return a :class:`LeaseExtraction` conforming to our schema.

The file is processed in-memory and never written to disk or Firebase — clients hold
the original and attach it to Firebase only when the reviewed form is submitted.
"""
import base64
import io
from dataclasses import dataclass
from datetime import date
from typing import Optional

from anthropic import Anthropic
from fastapi import HTTPException, status
from pydantic import ValidationError

from app.schemas.document_extraction import (
    ExtractedField,
    ExtractedProperty,
    ExtractedRenter,
    LeaseExtraction,
)

# A non-strict tool the model fills with the extracted data. We deliberately avoid
# structured outputs (messages.parse): the strict grammar compiler rejects a schema
# this large. The schema is still used to validate the tool's output afterwards.
_TOOL_NAME = "record_lease_extraction"
_EXTRACTION_TOOL = {
    "name": _TOOL_NAME,
    "description": "Record the structured property and renter data extracted from the lease document.",
    "input_schema": LeaseExtraction.model_json_schema(),
}

# USD per 1M tokens (input, output). Used for a rough cost estimate on the audit log.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
}


@dataclass
class ExtractionMeta:
    """Telemetry about one extraction call, stored on the audit log."""

    model: str
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    cache_read_tokens: Optional[int]
    cache_creation_tokens: Optional[int]
    estimated_cost_usd: Optional[float]
    fields_extracted: int
    low_confidence_count: int
    medium_confidence_count: int


@dataclass
class ExtractionResult:
    extraction: LeaseExtraction
    meta: ExtractionMeta

# MIME types Claude reads natively as images.
_IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_PDF_MEDIA_TYPE = "application/pdf"
_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# One descriptive line per field — the "field schema" Claude maps the document onto.
# Kept stable so it can be prompt-cached across requests.
_SYSTEM_PROMPT = """You extract structured data from rental lease / property contracts to pre-fill a property-management app's forms. Documents are often in Hebrew (right-to-left) and may mix Hebrew, English, and numbers in tables — read them carefully and preserve the correct values.

A single lease usually describes BOTH a property and a renter. Populate both sections from the one document. Leave a field's value null if the document does not contain it — never guess or invent values.

For every field set:
- value: the extracted value, or null if absent. Dates as ISO YYYY-MM-DD. Money/areas as plain numbers (no currency symbols or commas).
- confidence: "high" if the value is stated explicitly, "medium" if inferred, "low" if uncertain or derived loosely.
- source_text: ONLY when confidence is medium or low, the short verbatim snippet the value came from. Set it to null when confidence is high.

Property fields:
- address, city, zip_code: the property's street address, city, and postal code.
- type: one of apartment, house, commercial, garden_apartment, housing_unit.
- sq_ft: floor area as a number (whatever unit the document uses).
- number_of_rooms, floor: room count and floor number.
- apartment, block, plot: apartment/unit number and land registry block ("gush") / plot ("helka").
- parking_numbers: list of parking spot identifiers.
- property_owner: the landlord/owner's name.
- electricity_meter_number, electricity_account_number, water_meter_number, water_account_number: utility identifiers.
- property_tax, house_committee: periodic property tax ("arnona") and building-committee ("vaad bayit") amounts.
- inventory_notes: any inventory / contents description.

Renter fields:
- first_name, last_name: the tenant's given and family name.
- phone, email: tenant contact details.
- lease_start: the lease commencement date (ISO YYYY-MM-DD).
- payment_type: payment method described (e.g. bank transfer, checks).
- payment_day_of_month: day of month rent is due (1-31).
- insurance_type, insurance_amount: required insurance type and its amount.
- number_of_payments: installments per year (e.g. 12 for monthly).
- extra_contacts: additional contacts (e.g. guarantors), each {name, phone}.

Lease term — describe it as INTENT; the app rebuilds the year-by-year schedule from these:
- contract_term_years: number of binding (contract) years. option_years: number of renewal-option years.
- base_rent: the FIRST-YEAR MONTHLY rent (a single monthly figure, never annual).
- rent_escalation_mode: how the monthly rent changes each year — "none" (flat, same every year), "percent" (rises a fixed % each year), "fixed" (rises a fixed money amount each year), or "custom" (irregular per-year amounts that follow no single rule).
- rent_escalation_value: the percent (for "percent") or the money amount (for "fixed"). Null for "none"/"custom".
- lease_years: leave EMPTY unless rent_escalation_mode is "custom". When custom, list one row per lease year, each {amount: that year's MONTHLY rent in the SAME unit as base_rent, type: "contract" or "option"}, and set contract_term_years + option_years to equal the number of rows.

Return only the structured data."""


class DocumentExtractionService:
    def __init__(self, api_key: str, model: str):
        self._api_key = api_key
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def _client(self) -> Anthropic:
        if not self._api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Document extraction is not configured (missing ANTHROPIC_API_KEY).",
            )
        return Anthropic(api_key=self._api_key)

    def _build_content_block(self, file_bytes: bytes, content_type: str) -> dict:
        """Turn the upload into a single Claude content block, by file type."""
        media_type = (content_type or "").split(";")[0].strip().lower()

        if media_type == _PDF_MEDIA_TYPE:
            return {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": _PDF_MEDIA_TYPE,
                    "data": base64.standard_b64encode(file_bytes).decode(),
                },
            }

        if media_type in _IMAGE_MEDIA_TYPES:
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(file_bytes).decode(),
                },
            }

        if media_type == _DOCX_MEDIA_TYPE:
            text = self._docx_to_text(file_bytes)
            if not text.strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="The document appears to be empty.",
                )
            return {"type": "text", "text": text}

        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file type. Upload a PDF, DOCX, or image (JPEG/PNG/GIF/WebP).",
        )

    @staticmethod
    def _docx_to_text(file_bytes: bytes) -> str:
        """Extract paragraph and table text from a .docx, preserving reading order."""
        from docx import Document  # imported lazily so the dep is only needed at runtime

        document = Document(io.BytesIO(file_bytes))
        lines: list[str] = [p.text for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    lines.append(" | ".join(cells))
        return "\n".join(lines)

    def extract_lease(self, file_bytes: bytes, content_type: str) -> ExtractionResult:
        """Extract a property + renter draft from a lease document, with call telemetry."""
        content_block = self._build_content_block(file_bytes, content_type)
        client = self._client()

        # Use a NON-strict tool (not structured-outputs / messages.parse): the strict
        # grammar compiler rejects a schema this large ("compiled grammar is too large").
        # The model fills the tool's schema best-effort and we validate it ourselves.
        response = client.messages.create(
            model=self._model,
            max_tokens=8192,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[_EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[
                {
                    "role": "user",
                    "content": [
                        content_block,
                        {
                            "type": "text",
                            "text": "Extract the property and renter details from this lease document.",
                        },
                    ],
                }
            ],
        )

        if response.stop_reason == "refusal":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The document could not be processed.",
            )
        tool_block = next(
            (b for b in response.content if getattr(b, "type", None) == "tool_use"), None
        )
        if tool_block is None:
            # e.g. stop_reason == "max_tokens" — no complete tool call returned.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Could not extract structured data from the document.",
            )
        try:
            parsed = LeaseExtraction.model_validate(tool_block.input)
        except ValidationError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Could not extract structured data from the document.",
            )

        extraction = _clean_extraction(parsed)
        extracted, low, medium = _field_stats(extraction)
        usage = response.usage
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        cache_read = getattr(usage, "cache_read_input_tokens", None)
        cache_creation = getattr(usage, "cache_creation_input_tokens", None)
        meta = ExtractionMeta(
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            estimated_cost_usd=_estimate_cost(
                self._model, input_tokens, output_tokens, cache_read, cache_creation
            ),
            fields_extracted=extracted,
            low_confidence_count=low,
            medium_confidence_count=medium,
        )
        return ExtractionResult(extraction=extraction, meta=meta)


def _drop(field: ExtractedField) -> None:
    """Discard an extracted value that fails validation, so it never pre-fills a form.

    Structured outputs already guarantee field *types* and *enum* values, so this only
    needs to enforce the extra rules the JSON schema can't express (see the validators
    on RenterCreate / the clients' Zod). A value that fails one of those is more likely
    wrong than right — better a blank field the user fills than a pre-filled error.
    """
    field.value = None
    field.confidence = "low"


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except (ValueError, TypeError):
        return False


def _clean_property(p: ExtractedProperty) -> None:
    for f in (p.sq_ft, p.number_of_rooms, p.floor, p.property_tax, p.house_committee):
        if f.value is not None and f.value < 0:
            _drop(f)


def _clean_renter(r: ExtractedRenter) -> None:
    # Mirrors RenterCreate.payment_day_in_range (1..31).
    if r.payment_day_of_month.value is not None and not (1 <= r.payment_day_of_month.value <= 31):
        _drop(r.payment_day_of_month)
    # lease_start must be a real ISO date; the form/back end store it as a `date`.
    if r.lease_start.value is not None and not _is_iso_date(r.lease_start.value):
        _drop(r.lease_start)
    for f in (r.base_rent, r.insurance_amount, r.rent_escalation_value, r.contract_term_years, r.option_years):
        if f.value is not None and f.value < 0:
            _drop(f)
    if r.lease_years.value is not None:
        # Drop the whole list if any year's amount is negative — partial lease schedules
        # are confusing to review; the user re-enters a clean one.
        if any(ly.amount < 0 for ly in r.lease_years.value):
            _drop(r.lease_years)


def _strip_high_confidence_sources(section: ExtractedProperty | ExtractedRenter) -> None:
    """Drop source_text on high-confidence fields — the snippet is only shown for the
    uncertain fields the user reviews. (The prompt also asks the model to skip it, which
    saves output tokens; this just makes the result deterministic.)"""
    for field in vars(section).values():
        if isinstance(field, ExtractedField) and field.confidence == "high":
            field.source_text = None


def _clean_extraction(extraction: LeaseExtraction) -> LeaseExtraction:
    """Null out extracted values that wouldn't survive form/back-end validation, and
    keep source snippets only on the uncertain fields."""
    _clean_property(extraction.property)
    _clean_renter(extraction.renter)
    _strip_high_confidence_sources(extraction.property)
    _strip_high_confidence_sources(extraction.renter)
    return extraction


def _field_stats(extraction: LeaseExtraction) -> tuple[int, int, int]:
    """Count populated fields and how many were low/medium confidence (a quality signal)."""
    extracted = low = medium = 0
    for section in (extraction.property, extraction.renter):
        for field in vars(section).values():
            if isinstance(field, ExtractedField) and field.value is not None:
                extracted += 1
                if field.confidence == "low":
                    low += 1
                elif field.confidence == "medium":
                    medium += 1
    return extracted, low, medium


def _estimate_cost(
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    cache_read: Optional[int],
    cache_creation: Optional[int],
) -> Optional[float]:
    """Rough USD estimate (cache writes ~1.25x input, cache reads ~0.1x input)."""
    price = _PRICES.get(model)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    in_rate, out_rate = price
    cost = (
        input_tokens * in_rate
        + (cache_creation or 0) * in_rate * 1.25
        + (cache_read or 0) * in_rate * 0.10
        + output_tokens * out_rate
    ) / 1_000_000
    return round(cost, 6)

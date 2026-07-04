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
#
# DISABLED extraction fields — rarely stated in a lease, so we don't spend tokens extracting
# them. To re-enable one: uncomment its field in app/schemas/document_extraction.py AND paste
# its bullet back into the matching (Property / Renter) section of _SYSTEM_PROMPT below.
#   Property:
#   - zip_code: the property's postal code (part of the address/city/zip group).
#   - sq_ft: floor area as a number (whatever unit the document uses).
#   - parking_numbers: list of parking spot identifiers.
#   - block, plot: land registry block ("gush") / plot ("helka"). (Restore alongside `apartment`.)
#   - electricity_meter_number, electricity_account_number, water_meter_number, water_account_number: utility identifiers.
#   - property_tax, house_committee: periodic property tax ("arnona") and building-committee ("vaad bayit") amounts.
#   - inventory_notes: any inventory / contents description.
#   Renter:
#   - email: tenant email address.
_SYSTEM_PROMPT = """You extract structured data from rental lease / property contracts to pre-fill a property-management app's forms. Documents are often in Hebrew (right-to-left) and may mix Hebrew, English, and numbers in tables — read them carefully and preserve the correct values.

A single lease usually describes a property and one or more renters (co-tenants who sign the same lease). Populate the property once and add one entry to `renters` for EACH tenant. Fill EVERY field that the document states — a typical lease contains most of them. Leave a field null ONLY if the document genuinely doesn't contain it; never guess or invent values. Dates as ISO YYYY-MM-DD; money/areas as plain numbers (no currency symbols or commas).

Confidence: for any field you are NOT highly confident about (inferred, ambiguous, or loosely derived), append one entry to `notes` with its `section` ("property" or "renter"), `field` (the exact field name below), `renter_index` (for a renter field, the 0-based index of the renter in `renters`; null for a property field), `confidence` ("medium" or "low"), and `source_text` (the short verbatim snippet it came from). Do NOT add a note for a field you're confident about, and never add a note for a null field.

Property fields:
- address_evidence: FILL THIS FIRST, before `address`. Copy the short verbatim phrase from the document that states WHERE THE RENTED PROPERTY IS — the clause describing the המושכר / הדירה / הנכס (e.g. "הדירה ברחוב הרצל 12 תל אביב"). Then derive `address`/`city` from this same phrase. Null only if the document truly never states the property's location.
- address, city: the address of the PROPERTY BEING RENTED (the demised premises) — NEVER a person's address. Read carefully; this is the most important field:
  * The rented property is called המושכר / הדירה / הנכס / הממכר. Its address is stated where the lease describes WHAT is being rented — usually the opening recitals ("הואיל והמשכיר הינו בעל הזכויות בדירה ברחוב ___ בעיר ___") or a dedicated clause ("המושכר: דירה ברחוב ___") or the "הואיל" / "מבוא" section. Take `address` and `city` from there.
  * Do NOT use any address from the parties block / signature area (the "בין ___ לבין ___" part). There each party's name is followed by "ת.ז ___" and "מרחוב ___" / "מ___" — those are the LANDLORD's and the TENANT's OWN home/mailing addresses. They are commonly the FIRST addresses in the document; taking one of them by mistake is the single most common error — do not.
  * If several street addresses appear, choose the one tied to המושכר/הדירה/הנכס, not the one tied to a person's name or ת.ז. If still unsure, pick the most likely property address and add a `notes` entry for `address` with its source_text.
- type: one of apartment, house, commercial, garden_apartment, housing_unit.
- number_of_rooms, floor: room count and floor number.
- apartment: apartment/unit number.
- property_owner: the landlord/owner's name.

Renter fields — one `renters` entry PER tenant. If several people sign as tenants, list them all; do not merge them or push them into extra_contacts.
- first_name, last_name: the tenant's given and family name.
- phone: the tenant's PHONE number only. An Israeli phone is typically 9-10 digits starting with 0 (mobile 05X-XXXXXXX) or +972. Before filling this, check the number really looks like a phone. A 9-digit national ID number (תעודת זהות / ת"ז) is NOT a phone — if the only number you see near the tenant is an ID, leave phone null rather than putting the ID here.
- lease_start: the lease commencement date (ISO YYYY-MM-DD).
- payment_type: payment method described (e.g. bank transfer, checks).
- payment_day_of_month: day of month rent is due (1-31).
- insurance_type: the required security/collateral type — exactly one of "bank_guarantee" (ערבות בנקאית) or "wire_transfer" (פיקדון / העברה בנקאית / cash deposit). insurance_amount: its amount (usually stated right next to the type). Return null for insurance_type if it isn't one of those two.
- number_of_payments: installments per year (e.g. 12 for monthly).
- extra_contacts: NON-tenant contacts only, e.g. guarantors (ערבים), each {name, phone}. People who sign as tenants belong in `renters`, not here.

Joint vs. per-tenant rent: most leases with several tenants state ONE joint rent for all of them together (they are jointly liable, "ביחד ולחוד"), not a separate amount per tenant. When the rent is joint, set `rent_is_joint` true, put that single first-year MONTHLY total in top-level `joint_monthly_rent`, and leave every renter's `base_rent` null (still fill the shared lease-term fields — escalation, years, insurance, payment — identically on each renter). Only when the document gives each tenant their OWN distinct amount, set `rent_is_joint` false and fill each renter's own `base_rent`. For a single-tenant lease, set `rent_is_joint` false and fill that renter's `base_rent`.

Lease term — describe it as INTENT; the app rebuilds the year-by-year schedule from these:
- contract_term_years: number of binding (contract) years. option_years: number of renewal-option years.
- base_rent: the FIRST-YEAR MONTHLY rent (a single monthly figure, never annual). Leave null when rent_is_joint is true (use joint_monthly_rent instead).
- rent_escalation_mode: how the monthly rent changes each year — "none" (flat, same every year), "percent" (rises a fixed % each year), "fixed" (rises a fixed money amount each year), "cpi" (linked/indexed to the Consumer Price Index — מדד המחירים לצרכן / הצמדה למדד), or "custom" (irregular per-year amounts that follow no single rule). Use "cpi" whenever the rent is tied to the index (הצמדה למדד המחירים לצרכן), even if a minimum increase is also mentioned.
- rent_escalation_value: the percent (for "percent") or the money amount (for "fixed"). Null for "none"/"custom"/"cpi".
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


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except (ValueError, TypeError):
        return False


def _clean_property(p: ExtractedProperty) -> None:
    # getattr default None so a currently-disabled (commented-out) field is simply skipped.
    for name in ("sq_ft", "number_of_rooms", "floor", "property_tax", "house_committee"):
        v = getattr(p, name, None)
        if v is not None and v < 0:
            setattr(p, name, None)


# Security/collateral type the renter form accepts; other free text is dropped.
_SECURITY_TYPES = {"bank_guarantee", "wire_transfer"}


def _looks_like_israeli_id(value: str) -> bool:
    """A bare 9-digit number is almost certainly a national ID (ת\"ז), not a phone."""
    digits = "".join(ch for ch in value if ch.isdigit())
    return len(digits) == 9 and digits == value.strip()


def _clean_renter(r: ExtractedRenter) -> None:
    # Mirrors RenterCreate.payment_day_in_range (1..31).
    if r.payment_day_of_month is not None and not (1 <= r.payment_day_of_month <= 31):
        r.payment_day_of_month = None
    # lease_start must be a real ISO date; the form/back end store it as a `date`.
    if r.lease_start is not None and not _is_iso_date(r.lease_start):
        r.lease_start = None
    for name in ("base_rent", "insurance_amount", "rent_escalation_value", "contract_term_years", "option_years"):
        v = getattr(r, name)
        if v is not None and v < 0:
            setattr(r, name, None)
    # Drop the whole schedule if any year's amount is negative — a partial/garbled
    # schedule is confusing to review; the user re-enters a clean one.
    if r.lease_years is not None and any(ly.amount < 0 for ly in r.lease_years):
        r.lease_years = None
    # Insurance/security type must be one of the enum values the renter form accepts.
    if r.insurance_type is not None and r.insurance_type not in _SECURITY_TYPES:
        r.insurance_type = None
    # Guard against the national ID being mistaken for a phone (see the prompt).
    if r.phone is not None and _looks_like_israeli_id(r.phone):
        r.phone = None


def _clean_extraction(extraction: LeaseExtraction) -> LeaseExtraction:
    """Null out extracted values that wouldn't survive form/back-end validation, then
    drop any uncertainty notes whose field we just nulled."""
    _clean_property(extraction.property)
    for renter in extraction.renters:
        _clean_renter(renter)
    if extraction.joint_monthly_rent is not None and extraction.joint_monthly_rent < 0:
        extraction.joint_monthly_rent = None

    def _note_target(n):
        if n.section == "property":
            return extraction.property
        if n.renter_index is not None and 0 <= n.renter_index < len(extraction.renters):
            return extraction.renters[n.renter_index]
        return None

    extraction.notes = [
        n for n in extraction.notes
        if (_t := _note_target(n)) is not None and getattr(_t, n.field, None) is not None
    ]
    return extraction


def _field_stats(extraction: LeaseExtraction) -> tuple[int, int, int]:
    """Count populated fields, and low/medium-confidence counts from the notes."""
    extracted = 0
    for section in (extraction.property, *extraction.renters):
        for value in vars(section).values():
            if value is not None:
                extracted += 1
    low = sum(1 for n in extraction.notes if n.confidence == "low")
    medium = sum(1 for n in extraction.notes if n.confidence == "medium")
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

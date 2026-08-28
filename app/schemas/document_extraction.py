"""Schemas for the document-extraction feature (POST /extract/lease).

A lease/contract is sent to Claude, which returns a structured draft the clients use
to pre-fill the existing property and renter forms. The shape is deliberately **flat**
— a plain value per field — so the model can fill it reliably (a deeply nested
per-field wrapper caused severe under-filling and exceeded the strict-grammar limit).
Uncertainty is reported out-of-band via :class:`FieldNote`: the model adds a note ONLY
for a field it isn't highly confident about, carrying the confidence and the source
snippet. Fields without a note are treated as high-confidence (no review flag, no
snippet) — matching the cost optimisation (snippets only where the user must verify).

These schemas mirror the *subset* of fields collected by the property and renter forms
(see ``app.schemas.property.PropertyCreate`` / ``app.schemas.renter.RenterCreate``).
Server-managed fields (``owner_id``) and file URLs are excluded — the clients attach
files to Firebase at submit time, not during extraction.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.property import PropertyType
from app.schemas.renter import LeaseYearType, RentEscalationMode

Confidence = Literal["medium", "low"]


class LeaseYearGuess(BaseModel):
    """Mirror of ``app.schemas.renter.LeaseYear`` for extraction."""

    amount: float
    type: LeaseYearType
    # Absent means a full twelve months, as everywhere else.
    months: Optional[int] = None


class ExtraContactGuess(BaseModel):
    """Mirror of ``app.schemas.renter.ExtraContact`` for extraction."""

    name: str
    phone: str


class ExtractedProperty(BaseModel):
    """Property fields extractable from a lease (plain values; null if absent)."""

    # Emitted FIRST (before `address`) so the model must locate the clause describing the
    # rented property before committing to an address — this is the field order the tool
    # schema exposes, and it markedly improves picking the property's address over a party's
    # home address. Not shown in the forms; used only as the model's own grounding + a note.
    address_evidence: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    type: Optional[PropertyType] = None
    number_of_rooms: Optional[float] = None
    floor: Optional[int] = None
    apartment: Optional[str] = None
    property_owner: Optional[str] = None
    # --- DISABLED fields (rarely stated in a lease → not worth the extraction tokens).
    # To re-enable, uncomment here AND move the matching bullet back into _SYSTEM_PROMPT
    # (see the "DISABLED extraction fields" block in document_extraction_service.py).
    # zip_code: Optional[str] = None
    # sq_ft: Optional[int] = None
    # parking_numbers: Optional[list[str]] = None
    # block: Optional[str] = None
    # plot: Optional[str] = None
    # electricity_meter_number: Optional[str] = None
    # electricity_account_number: Optional[str] = None
    # water_meter_number: Optional[str] = None
    # water_account_number: Optional[str] = None
    # property_tax: Optional[float] = None
    # house_committee: Optional[float] = None
    # inventory_notes: Optional[str] = None


class ExtractedRenter(BaseModel):
    """Renter / lease-term fields extractable from a lease (plain values; null if absent)."""

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    lease_start: Optional[str] = None  # ISO date (YYYY-MM-DD)
    lease_years: Optional[list[LeaseYearGuess]] = None
    contract_term_years: Optional[int] = None
    # Months on top of the whole years, for a term the lease states as e.g. "two years
    # and four months". Israeli leases do write odd terms, and rounding one to whole
    # years would put the end date — and every renewal reminder — in the wrong month.
    contract_term_months: Optional[int] = None
    option_years: Optional[int] = None
    option_term_months: Optional[int] = None
    base_rent: Optional[float] = None
    rent_escalation_mode: Optional[RentEscalationMode] = None
    rent_escalation_value: Optional[float] = None
    number_of_payments: Optional[int] = None
    payment_type: Optional[str] = None
    # Bounds are published to the model via the tool's JSON schema, but deliberately NOT
    # enforced with ge/le: a validator here would raise in LeaseExtraction.model_validate()
    # and fail the WHOLE scan over one bad field. _clean_renter nulls it instead.
    payment_day_of_month: Optional[int] = Field(
        default=None, json_schema_extra={"minimum": 1, "maximum": 31}
    )
    # Free text from the model, normalised to one of the security-type enum values
    # ("bank_guarantee" / "wire_transfer") or nulled in _clean_renter.
    insurance_type: Optional[str] = None
    insurance_amount: Optional[float] = None
    extra_contacts: Optional[list[ExtraContactGuess]] = None


class FieldNote(BaseModel):
    """Uncertainty note for a single extracted field the model wasn't sure about."""

    section: Literal["property", "renter"]
    field: str  # snake field name, e.g. "city" or "base_rent"
    # Which renter the note refers to (index into ``renters``); null for property notes.
    renter_index: Optional[int] = None
    confidence: Confidence
    source_text: Optional[str] = None


class LeaseExtraction(BaseModel):
    """The full structured draft returned by POST /extract/lease.

    A single lease often contains a property and one or more renters (co-tenants),
    so all are populated from one upload. Any part may be empty if the document only
    covers one. ``notes`` lists only the fields the model was unsure about.

    When the lease states a single joint rent for all tenants together (rather than a
    separate amount per tenant), ``rent_is_joint`` is true and ``joint_monthly_rent``
    holds that first-year monthly total; the client lets the user split it across the
    renters. When false, each renter carries their own ``base_rent``.
    """

    property: ExtractedProperty = ExtractedProperty()
    renters: list[ExtractedRenter] = []
    rent_is_joint: bool = False
    joint_monthly_rent: Optional[float] = None
    notes: list[FieldNote] = []


class LeaseExtractionResponse(BaseModel):
    """POST /extract/lease response — the draft plus the audit-log id the client
    references when reporting the submit outcome."""

    log_id: int
    extraction: LeaseExtraction


class FieldEdit(BaseModel):
    """One prefilled field the user overrode, for the audit log."""

    field: str  # i18n label key
    prefilled_value: str
    submitted_value: str
    source_text: Optional[str] = None


class ExtractionLogUpdate(BaseModel):
    """PATCH /extract/logs/{id} body — sent once per form the user submits."""

    model_config = ConfigDict(protected_namespaces=())

    entity_type: Literal["property", "renter"]
    created_id: Optional[int] = None
    contract_url: Optional[str] = None
    fields_given_count: int = 0
    field_edits: list[FieldEdit] = []

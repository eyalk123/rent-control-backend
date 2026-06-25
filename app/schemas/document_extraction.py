"""Schemas for the document-extraction feature (POST /extract/lease).

A lease/contract is sent to Claude, which returns a structured draft that the
clients use to pre-fill the existing property and renter forms. Every extractable
field is wrapped in :class:`ExtractedField` so the UI can show a confidence flag
and the source snippet the value came from, and let the user review before saving.

These schemas mirror the *subset* of fields collected by the property and renter
forms (see ``app.schemas.property.PropertyCreate`` /
``app.schemas.renter.RenterCreate``). Server-managed fields (``owner_id``) and
file URLs (``basic_contract_url`` / ``full_contract_url`` / ``id_image_url`` /
``image_url``) are intentionally excluded — the clients attach files to Firebase
at submit time, not during extraction.
"""
from typing import Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

from app.schemas.property import PropertyType
from app.schemas.renter import LeaseYearType, RentEscalationMode

T = TypeVar("T")

Confidence = Literal["high", "medium", "low"]


class ExtractedField(BaseModel, Generic[T]):
    """One extracted value plus how sure the model is and where it came from.

    ``value`` is ``None`` when the document does not contain the field. The clients
    highlight ``confidence == "low"`` so the user double-checks those before saving.
    """

    value: Optional[T] = None
    confidence: Confidence = "low"
    source_text: Optional[str] = None


class LeaseYearGuess(BaseModel):
    """Mirror of ``app.schemas.renter.LeaseYear`` for extraction."""

    amount: float
    type: LeaseYearType


class ExtraContactGuess(BaseModel):
    """Mirror of ``app.schemas.renter.ExtraContact`` for extraction."""

    name: str
    phone: str


class ExtractedProperty(BaseModel):
    """Property fields extractable from a lease, each with confidence + source."""

    address: ExtractedField[str] = ExtractedField()
    city: ExtractedField[str] = ExtractedField()
    zip_code: ExtractedField[str] = ExtractedField()
    type: ExtractedField[PropertyType] = ExtractedField()
    sq_ft: ExtractedField[int] = ExtractedField()
    number_of_rooms: ExtractedField[int] = ExtractedField()
    parking_numbers: ExtractedField[list[str]] = ExtractedField()
    floor: ExtractedField[int] = ExtractedField()
    apartment: ExtractedField[str] = ExtractedField()
    block: ExtractedField[str] = ExtractedField()
    plot: ExtractedField[str] = ExtractedField()
    property_owner: ExtractedField[str] = ExtractedField()
    electricity_meter_number: ExtractedField[str] = ExtractedField()
    electricity_account_number: ExtractedField[str] = ExtractedField()
    water_meter_number: ExtractedField[str] = ExtractedField()
    water_account_number: ExtractedField[str] = ExtractedField()
    property_tax: ExtractedField[float] = ExtractedField()
    house_committee: ExtractedField[float] = ExtractedField()
    inventory_notes: ExtractedField[str] = ExtractedField()


class ExtractedRenter(BaseModel):
    """Renter / lease-term fields extractable from a lease."""

    first_name: ExtractedField[str] = ExtractedField()
    last_name: ExtractedField[str] = ExtractedField()
    phone: ExtractedField[str] = ExtractedField()
    email: ExtractedField[str] = ExtractedField()
    lease_start: ExtractedField[str] = ExtractedField()  # ISO date (YYYY-MM-DD)
    lease_years: ExtractedField[list[LeaseYearGuess]] = ExtractedField()
    contract_term_years: ExtractedField[int] = ExtractedField()
    option_years: ExtractedField[int] = ExtractedField()
    base_rent: ExtractedField[float] = ExtractedField()
    rent_escalation_mode: ExtractedField[RentEscalationMode] = ExtractedField()
    rent_escalation_value: ExtractedField[float] = ExtractedField()
    number_of_payments: ExtractedField[int] = ExtractedField()
    payment_type: ExtractedField[str] = ExtractedField()
    payment_day_of_month: ExtractedField[int] = ExtractedField()
    insurance_type: ExtractedField[str] = ExtractedField()
    insurance_amount: ExtractedField[float] = ExtractedField()
    extra_contacts: ExtractedField[list[ExtraContactGuess]] = ExtractedField()


class LeaseExtraction(BaseModel):
    """The full structured draft returned by POST /extract/lease.

    A single lease often contains both the property and the renter, so both are
    populated from one upload. Either may be empty if the document only covers one.
    """

    property: ExtractedProperty = ExtractedProperty()
    renter: ExtractedRenter = ExtractedRenter()


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

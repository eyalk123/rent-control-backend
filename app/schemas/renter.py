import json
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.property import PropertyBriefRead


class LeaseYearType(str, Enum):
    option = "option"
    contract = "contract"


class LeaseYear(BaseModel):
    amount: float
    type: LeaseYearType


class ExtraContact(BaseModel):
    name: str
    phone: str


def _parse_lease_years(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        if not v.strip():
            return []
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return []
    return []


class RenterCreate(BaseModel):
    property_id: Optional[int] = None
    first_name: str
    last_name: str
    phone: str
    email: str
    lease_years: list[LeaseYear]
    lease_start: date
    number_of_payments: Optional[int] = None
    payment_type: Optional[str] = None
    payment_day_of_month: Optional[int] = None
    insurance_type: Optional[str] = None
    insurance_amount: Optional[float] = None
    contact_id: Optional[str] = None
    extra_contacts: Optional[list[ExtraContact]] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("payment_day_of_month")
    @classmethod
    def payment_day_in_range(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 1 or v > 31):
            raise ValueError("payment_day_of_month must be between 1 and 31")
        return v


class RenterUpdate(BaseModel):
    property_id: Optional[int] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    lease_years: Optional[list[LeaseYear]] = None
    lease_start: Optional[date] = None
    number_of_payments: Optional[int] = None
    payment_type: Optional[str] = None
    payment_day_of_month: Optional[int] = None
    insurance_type: Optional[str] = None
    insurance_amount: Optional[float] = None
    contact_id: Optional[str] = None
    extra_contacts: Optional[list[ExtraContact]] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("payment_day_of_month")
    @classmethod
    def payment_day_in_range(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 1 or v > 31):
            raise ValueError("payment_day_of_month must be between 1 and 31")
        return v


class RenterRead(BaseModel):
    id: int
    property_id: Optional[int] = None
    first_name: str
    last_name: str
    phone: str
    email: str
    lease_years: list[LeaseYear]
    lease_start: date
    number_of_payments: Optional[int] = None
    payment_type: Optional[str] = None
    payment_day_of_month: Optional[int] = None
    insurance_type: Optional[str] = None
    insurance_amount: Optional[float] = None
    contact_id: Optional[str] = None
    extra_contacts: Optional[list[ExtraContact]] = None
    property: Optional[PropertyBriefRead] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("lease_years", mode="before")
    @classmethod
    def parse_lease_years(cls, v):
        return _parse_lease_years(v)


class RenterListRead(RenterRead):
    """Same as RenterRead - used for list endpoint with property brief."""

    pass


class PropertyRenterSummary(BaseModel):
    """Minimal renter info for property renters list (e.g. add-revenue form)."""

    id: int
    first_name: str
    last_name: str
    monthly_rent: float

    model_config = ConfigDict(from_attributes=True)

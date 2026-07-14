import json
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_serializer, model_validator

from app.schemas.property import PropertyBriefRead


class LeaseYearType(str, Enum):
    option = "option"
    contract = "contract"


class RentEscalationMode(str, Enum):
    none = "none"
    percent = "percent"
    fixed = "fixed"
    custom = "custom"
    cpi = "cpi"


class LeaseYearRuleMode(str, Enum):
    """How one lease year's rent derives from the *previous* year's amount. Only
    meaningful under ``rent_escalation_mode == 'custom'``, where each year carries its
    own rule instead of the whole lease sharing one."""

    manual = "manual"  # the owner typed this amount; never derived
    none = "none"  # same as the previous year
    percent = "percent"
    fixed = "fixed"
    cpi = "cpi"


class LeaseYearRule(BaseModel):
    mode: LeaseYearRuleMode
    value: Optional[float] = None

    @model_validator(mode="after")
    def value_required_for_stepped_modes(self) -> "LeaseYearRule":
        if self.mode in (LeaseYearRuleMode.percent, LeaseYearRuleMode.fixed) and self.value is None:
            raise ValueError(f"rule.value is required when rule.mode is '{self.mode.value}'")
        return self


class LeaseYear(BaseModel):
    amount: float
    type: LeaseYearType
    # Absent on the first year (it is the base rent) and on every year of a
    # non-custom lease — which is also why every pre-existing stored year has none.
    rule: Optional[LeaseYearRule] = None

    @model_serializer
    def serialize(self) -> dict:
        """Drop `rule` entirely when there isn't one, rather than emitting `rule: null`.
        Keeps both the stored JSON blob and the API response byte-identical to their
        pre-rules shape for every lease that doesn't use per-year rules."""
        data: dict = {"amount": self.amount, "type": self.type.value}
        if self.rule is not None:
            data["rule"] = self.rule.model_dump(exclude_none=True)
        return data


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
    email: Optional[str] = None
    lease_years: list[LeaseYear]
    lease_start: Optional[date] = None
    contract_term_years: Optional[int] = None
    option_years: Optional[int] = None
    base_rent: Optional[float] = None
    rent_escalation_mode: Optional[RentEscalationMode] = None
    rent_escalation_value: Optional[float] = None
    number_of_payments: Optional[int] = None
    payment_type: Optional[str] = None
    payment_day_of_month: Optional[int] = None
    insurance_type: Optional[str] = None
    insurance_amount: Optional[float] = None
    contact_id: Optional[str] = None
    extra_contacts: Optional[list[ExtraContact]] = None
    full_contract_url: Optional[str] = None
    id_image_url: Optional[str] = None

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
    contract_term_years: Optional[int] = None
    option_years: Optional[int] = None
    base_rent: Optional[float] = None
    rent_escalation_mode: Optional[RentEscalationMode] = None
    rent_escalation_value: Optional[float] = None
    number_of_payments: Optional[int] = None
    payment_type: Optional[str] = None
    payment_day_of_month: Optional[int] = None
    insurance_type: Optional[str] = None
    insurance_amount: Optional[float] = None
    contact_id: Optional[str] = None
    extra_contacts: Optional[list[ExtraContact]] = None
    full_contract_url: Optional[str] = None
    id_image_url: Optional[str] = None

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
    email: Optional[str] = None
    lease_years: list[LeaseYear]
    lease_start: Optional[date] = None
    contract_term_years: Optional[int] = None
    option_years: Optional[int] = None
    base_rent: Optional[float] = None
    rent_escalation_mode: Optional[RentEscalationMode] = None
    rent_escalation_value: Optional[float] = None
    cpi_base_index: Optional[float] = None  # server-set; read-only
    number_of_payments: Optional[int] = None
    payment_type: Optional[str] = None
    payment_day_of_month: Optional[int] = None
    insurance_type: Optional[str] = None
    insurance_amount: Optional[float] = None
    contact_id: Optional[str] = None
    extra_contacts: Optional[list[ExtraContact]] = None
    full_contract_url: Optional[str] = None
    id_image_url: Optional[str] = None
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


class OverdueRenterRead(BaseModel):
    renter_id: int
    first_name: str
    last_name: str
    property_id: Optional[int]
    property_address: Optional[str]
    property_city: Optional[str]
    property_owner: Optional[str]
    monthly_amount: float
    payment_day_of_month: Optional[int]
    payment_type: Optional[str]
    days_overdue: int

    model_config = ConfigDict(from_attributes=True)


class ExpiringRenterRead(BaseModel):
    renter_id: int
    first_name: str
    last_name: str
    property_id: Optional[int]
    property_address: Optional[str]
    property_city: Optional[str]
    property_owner: Optional[str]
    lease_end_date: date
    days_until_expiry: int

    model_config = ConfigDict(from_attributes=True)

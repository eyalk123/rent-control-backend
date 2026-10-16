import json
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.schemas.property_mortgage import MortgageCreate, MortgageRead
from app.schemas.property_valuation import PropertyValuationCreate
from app.schemas.stored_file import StoredFile


class PropertyType(str, Enum):
    Apartment = "apartment"
    House = "house"
    Commercial = "commercial"
    GardenApartment = "garden_apartment"  # Israel only
    HousingUnit = "housing_unit"  # Israel only
    CondoTownhouse = "condo_townhouse"
    Penthouse = "penthouse"
    Room = "room"
    Other = "other"


def _normalize_optional_str(v):
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    return v


class PropertyBriefRead(BaseModel):
    """Minimal property info for nested use in renter responses."""

    id: int
    address: str
    city: str
    type: PropertyType
    floor: Optional[int] = None
    apartment: Optional[str] = None
    image_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PropertyCreate(BaseModel):
    address: str
    city: str
    zip_code: Optional[str] = None
    type: PropertyType
    sq_ft: Optional[int] = None
    purchase_price: Optional[float] = None
    purchase_date: Optional[date] = None
    # A new entry for the value history, added by the form's "+" before the save. Appended,
    # never a replacement: earlier values stay in the history.
    valuation: Optional[PropertyValuationCreate] = None
    # The form's mortgage switch, on. Israel only (422 elsewhere).
    mortgage: Optional[MortgageCreate] = None
    image_url: StoredFile = None
    number_of_rooms: Optional[float] = None
    parking_numbers: Optional[list[str]] = None
    electricity_meter_number: Optional[str] = None
    electricity_account_number: Optional[str] = None
    water_meter_number: Optional[str] = None
    water_account_number: Optional[str] = None
    property_tax: Optional[float] = None
    house_committee: Optional[float] = None
    # The owner record. `property_owner` is the older way to say it — a name, matched
    # exactly or created — kept because mobile builds already in the stores send it.
    # When both are sent the id wins.
    property_owner_id: Optional[int] = None
    property_owner: Optional[str] = None
    inventory_notes: Optional[str] = None
    basic_contract_url: StoredFile = None
    land_registry_url: StoredFile = None
    floor: Optional[int] = None
    apartment: Optional[str] = None
    block: Optional[str] = None
    plot: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("property_owner", mode="before")
    @classmethod
    def normalize_property_owner_create(cls, v):
        return _normalize_optional_str(v)

    @field_validator("purchase_price")
    @classmethod
    def normalize_purchase_price_create(cls, v):
        return _normalize_purchase_price(v)


class PropertyUpdate(BaseModel):
    address: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    type: Optional[PropertyType] = None
    sq_ft: Optional[int] = None
    purchase_price: Optional[float] = None
    purchase_date: Optional[date] = None
    # A new entry for the value history, added by the form's "+" before the save. Appended,
    # never a replacement: earlier values stay in the history.
    valuation: Optional[PropertyValuationCreate] = None
    # The form's mortgage switch. Omitted: the mortgage is left alone (older mobile builds
    # never send it). An object: creates the mortgage, or corrects it — the amount in force
    # today is overwritten, not added to the history. `null`: the switch was turned off, and
    # the mortgage and its history are deleted.
    mortgage: Optional[MortgageCreate] = None
    image_url: StoredFile = None
    number_of_rooms: Optional[float] = None
    parking_numbers: Optional[list[str]] = None
    electricity_meter_number: Optional[str] = None
    electricity_account_number: Optional[str] = None
    water_meter_number: Optional[str] = None
    water_account_number: Optional[str] = None
    property_tax: Optional[float] = None
    house_committee: Optional[float] = None
    # The owner record. `property_owner` is the older way to say it — a name, matched
    # exactly or created — kept because mobile builds already in the stores send it.
    # When both are sent the id wins.
    property_owner_id: Optional[int] = None
    property_owner: Optional[str] = None
    inventory_notes: Optional[str] = None
    basic_contract_url: StoredFile = None
    land_registry_url: StoredFile = None
    floor: Optional[int] = None
    apartment: Optional[str] = None
    block: Optional[str] = None
    plot: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("property_owner", mode="before")
    @classmethod
    def normalize_property_owner_update(cls, v):
        return _normalize_optional_str(v)

    @field_validator("purchase_price")
    @classmethod
    def normalize_purchase_price_update(cls, v):
        return _normalize_purchase_price(v)


def _normalize_purchase_price(v):
    """0 means "not entered" (an old mobile build sent it for a blank field), so it is stored
    as no price at all; a negative price is a typo."""
    if v is None:
        return None
    if v < 0:
        raise ValueError("purchase_price cannot be negative")
    return v or None


def _parse_parking_numbers(v):
    if v is None:
        return None
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        if not v.strip():
            return None
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return None
    return None


class PropertyReturnsRead(BaseModel):
    """Yields and appreciation, computed by `app.services.property_returns` — the one place
    they are calculated. Ratios are fractions (0.042 is 4.2%). A figure is null when an
    input is missing, and `unavailable` maps its name to why (e.g. "no_current_value")."""

    gross_yield: Optional[float] = None
    net_yield: Optional[float] = None
    appreciation: Optional[float] = None
    appreciation_pct: Optional[float] = None
    appreciation_per_year: Optional[float] = None
    annual_rent: Optional[float] = None
    net_income_12m: Optional[float] = None
    window_start: Optional[date] = None
    window_end: Optional[date] = None
    # Cash flow after the mortgage, per month — see `property_returns._cash_flow`.
    # All null for a property without a mortgage, with no `unavailable` entry for them.
    monthly_mortgage: Optional[float] = None
    monthly_expenses: Optional[float] = None
    monthly_left: Optional[float] = None
    mortgage_coverage: Optional[float] = None
    unavailable: dict[str, str] = {}

    model_config = ConfigDict(from_attributes=True)


# Import after PropertyBriefRead is defined to avoid circular import
from app.schemas.renter import RenterRead


class PropertyRead(BaseModel):
    id: int
    owner_id: str
    address: str
    city: str
    # Optional on the way out as well as in. A property saved without them is now a valid
    # row, and a Read model that still required them would raise a 500 when serialising it.
    zip_code: Optional[str] = None
    type: PropertyType
    sq_ft: Optional[int] = None
    purchase_price: Optional[float] = None
    purchase_date: Optional[date] = None
    # The latest entry of the value history (see PropertyValuation), or None if there is none.
    current_value: Optional[float] = None
    current_value_date: Optional[date] = None
    # The property's mortgage, or None — which is what the form's mortgage switch reads.
    mortgage: Optional[MortgageRead] = None
    # Set by PropertyService on reads (list and detail), not stored. Null on a locked stub
    # and on the write responses.
    returns: Optional[PropertyReturnsRead] = None
    image_url: Optional[str] = None
    number_of_rooms: Optional[float] = None
    parking_numbers: Optional[list[str]] = None
    electricity_meter_number: Optional[str] = None
    electricity_account_number: Optional[str] = None
    water_meter_number: Optional[str] = None
    water_account_number: Optional[str] = None
    property_tax: Optional[float] = None
    house_committee: Optional[float] = None
    property_owner_id: Optional[int] = None
    property_owner: Optional[str] = None  # the owner's name
    inventory_notes: Optional[str] = None
    basic_contract_url: Optional[str] = None
    land_registry_url: Optional[str] = None
    floor: Optional[int] = None
    apartment: Optional[str] = None
    block: Optional[str] = None
    plot: Optional[str] = None
    renters: Optional[list[RenterRead]] = None
    hasRenters: Optional[bool] = None
    # Over the plan's property ceiling. A locked property reaches the client only as a stub
    # in the list (see PropertyService._stub); every other read of it is a 402. Set by
    # PropertyService from the entitlement gate, not stored — it is a fact about the
    # account's plan today, not about the property.
    #
    # Carried on the property rather than left for clients to derive from a separate list
    # of locked ids: both clients then read one field, the list and the detail screens
    # cannot disagree, and there is no window where one of them is stale.
    locked: bool = False

    model_config = ConfigDict(from_attributes=True)

    @field_validator("parking_numbers", mode="before")
    @classmethod
    def parse_parking_numbers(cls, v):
        return _parse_parking_numbers(v)

    @model_validator(mode="after")
    def compute_has_renters(self):
        """Occupied = at least one tenancy that has not finished.

        ``renters`` deliberately carries the past tenants too — they are the record of who
        was here and what they paid, and the apps list them under "previous tenants" — so
        a bare ``len(...) > 0`` marked a long-empty flat as occupied forever, from the
        first lease it ever had.
        """
        # Imported here, not at module scope: `app.services.__init__` pulls in
        # `property_service`, which imports this module back.
        from app.services.lease_periods import is_tenancy_ended

        if self.hasRenters is None and self.renters is not None:
            today = date.today()
            self.hasRenters = any(
                not is_tenancy_ended(r.terminated_on, r.lease_end, today)
                for r in self.renters
            )
        return self


PropertyListRead = PropertyRead

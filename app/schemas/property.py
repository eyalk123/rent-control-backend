import json
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class PropertyType(str, Enum):
    Apartment = "apartment"
    House = "house"
    Commercial = "commercial"


class PropertyBriefRead(BaseModel):
    """Minimal property info for nested use in renter responses."""

    id: int
    address: str
    city: str
    type: PropertyType

    model_config = ConfigDict(from_attributes=True)


class PropertyCreate(BaseModel):
    address: str
    city: str
    zip_code: str
    type: PropertyType
    sq_ft: int
    purchase_price: float
    image_url: Optional[str] = None
    number_of_rooms: Optional[int] = None
    parking_numbers: Optional[list[str]] = None
    electricity_meter_number: Optional[str] = None
    water_meter_tax: Optional[float] = None
    property_tax: Optional[float] = None
    house_committee: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class PropertyUpdate(BaseModel):
    address: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    type: Optional[PropertyType] = None
    sq_ft: Optional[int] = None
    purchase_price: Optional[float] = None
    image_url: Optional[str] = None
    number_of_rooms: Optional[int] = None
    parking_numbers: Optional[list[str]] = None
    electricity_meter_number: Optional[str] = None
    water_meter_tax: Optional[float] = None
    property_tax: Optional[float] = None
    house_committee: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


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


# Import after PropertyBriefRead is defined to avoid circular import
from app.schemas.renter import RenterRead


class PropertyRead(BaseModel):
    id: int
    owner_id: int
    address: str
    city: str
    zip_code: str
    type: PropertyType
    sq_ft: int
    purchase_price: float
    image_url: Optional[str] = None
    number_of_rooms: Optional[int] = None
    parking_numbers: Optional[list[str]] = None
    electricity_meter_number: Optional[str] = None
    water_meter_tax: Optional[float] = None
    property_tax: Optional[float] = None
    house_committee: Optional[float] = None
    renters: Optional[list[RenterRead]] = None
    hasRenters: Optional[bool] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("parking_numbers", mode="before")
    @classmethod
    def parse_parking_numbers(cls, v):
        return _parse_parking_numbers(v)

    @model_validator(mode="after")
    def compute_has_renters(self):
        if self.hasRenters is None and self.renters is not None:
            self.hasRenters = len(self.renters) > 0
        return self


PropertyListRead = PropertyRead

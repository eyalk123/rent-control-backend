from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict


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

    model_config = ConfigDict(from_attributes=True)


class PropertyUpdate(BaseModel):
    address: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    type: Optional[PropertyType] = None
    sq_ft: Optional[int] = None
    purchase_price: Optional[float] = None
    image_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PropertyListRead(BaseModel):
    id: int
    owner_id: int
    address: str
    city: str
    zip_code: str
    type: PropertyType
    sq_ft: int
    purchase_price: float
    image_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


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
    renters: list[RenterRead] = []

    model_config = ConfigDict(from_attributes=True)

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.property import PropertyBriefRead


class RenterCreate(BaseModel):
    property_id: Optional[int] = None
    first_name: str
    last_name: str
    phone: str
    email: str
    monthly_rent: float
    lease_start: date
    lease_end: date

    model_config = ConfigDict(from_attributes=True)


class RenterUpdate(BaseModel):
    property_id: Optional[int] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    monthly_rent: Optional[float] = None
    lease_start: Optional[date] = None
    lease_end: Optional[date] = None

    model_config = ConfigDict(from_attributes=True)


class RenterRead(BaseModel):
    id: int
    property_id: Optional[int] = None
    first_name: str
    last_name: str
    phone: str
    email: str
    monthly_rent: float
    lease_start: date
    lease_end: date
    property: Optional[PropertyBriefRead] = None

    model_config = ConfigDict(from_attributes=True)


class RenterListRead(RenterRead):
    """Same as RenterRead - used for list endpoint with property brief."""

    pass

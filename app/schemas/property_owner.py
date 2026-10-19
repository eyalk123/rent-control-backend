from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.clock import utc_today


class PropertyOwnerRead(BaseModel):
    id: int
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    bank_account: Optional[str] = None
    is_active: bool
    is_company: bool = False
    birth_date: Optional[date] = None
    over_60: bool = False
    # Server-set: the day `over_60` was ticked, which is the year it counts from.
    over_60_marked_on: Optional[date] = None
    # How many properties point at this owner. An owner still holding properties cannot be
    # deleted, so the clients need this to say why rather than offer a delete that fails.
    property_count: int = 0

    model_config = ConfigDict(from_attributes=True)


def _trim(v):
    return v.strip() if isinstance(v, str) else v


def _check_birth_date(v: Optional[date]) -> Optional[date]:
    if v is not None and not (date(1900, 1, 1) <= v <= utc_today()):
        raise ValueError("birth_date must be a past date")
    return v


class PropertyOwnerCreate(BaseModel):
    name: str = Field(..., min_length=1)
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    bank_account: Optional[str] = Field(None, max_length=50)
    is_company: bool = False
    birth_date: Optional[date] = None
    over_60: bool = False

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, v):
        return _trim(v)

    @field_validator("birth_date")
    @classmethod
    def birth_date_in_the_past(cls, v):
        return _check_birth_date(v)


class PropertyOwnerUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    bank_account: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None
    is_company: Optional[bool] = None
    birth_date: Optional[date] = None
    over_60: Optional[bool] = None

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, v):
        return _trim(v)

    @field_validator("birth_date")
    @classmethod
    def birth_date_in_the_past(cls, v):
        return _check_birth_date(v)

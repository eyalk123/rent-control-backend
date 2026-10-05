from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PropertyOwnerRead(BaseModel):
    id: int
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    bank_account: Optional[str] = None
    is_active: bool
    # How many properties point at this owner. An owner still holding properties cannot be
    # deleted, so the clients need this to say why rather than offer a delete that fails.
    property_count: int = 0

    model_config = ConfigDict(from_attributes=True)


def _trim(v):
    return v.strip() if isinstance(v, str) else v


class PropertyOwnerCreate(BaseModel):
    name: str = Field(..., min_length=1)
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    bank_account: Optional[str] = Field(None, max_length=50)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, v):
        return _trim(v)


class PropertyOwnerUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    bank_account: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, v):
        return _trim(v)

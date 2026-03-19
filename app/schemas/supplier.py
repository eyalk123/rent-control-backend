from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SupplierRead(BaseModel):
    id: int
    category_ids: list[int]
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="wrap")
    @classmethod
    def from_orm_supplier(cls, data: Any, handler: Any):
        """Convert ORM Supplier (with categories relationship) to SupplierRead."""
        if hasattr(data, "categories") and hasattr(data, "__dict__"):
            return cls(
                id=data.id,
                category_ids=[c.id for c in data.categories],
                name=data.name,
                phone=data.phone,
                email=data.email,
                notes=data.notes,
                is_active=data.is_active,
            )
        return handler(data)


class SupplierCreate(BaseModel):
    name: str = Field(..., min_length=1)
    category_ids: list[int] = Field(..., min_length=1)
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, v: str) -> str:
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("name")
    @classmethod
    def name_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name must be non-empty")
        return v


class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    category_ids: Optional[list[int]] = None
    is_active: Optional[bool] = None

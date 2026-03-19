from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExpenseCategoryRead(BaseModel):
    id: int
    key: Optional[str] = None
    name: Optional[str] = None
    is_active: bool
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class ExpenseCategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)

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

from datetime import date

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.utc import UtcDatetime


class PropertyValuationCreate(BaseModel):
    value: float
    valued_on: date

    @field_validator("value")
    @classmethod
    def value_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("value must be greater than 0")
        return v


class PropertyValuationRead(BaseModel):
    id: int
    property_id: int
    value: float
    valued_on: date
    created_at: UtcDatetime

    model_config = ConfigDict(from_attributes=True)

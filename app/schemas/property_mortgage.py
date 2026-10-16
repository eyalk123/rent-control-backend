from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.utc import UtcDatetime


def _positive(v: float) -> float:
    if v <= 0:
        raise ValueError("amount must be greater than 0")
    return v


class MortgageCreate(BaseModel):
    # Principal and interest together — the figure on the bank statement.
    monthly_payment: float
    # When that payment applies from. Omitted means today: the owner is saying what they
    # pay now, and nothing before today is computed from it yet.
    payment_from: Optional[date] = None
    charge_day: int = Field(ge=1, le=31)
    end_date: Optional[date] = None

    _monthly_payment_positive = field_validator("monthly_payment")(_positive)


class MortgageUpdate(BaseModel):
    """The mortgage's own fields. The payment changes through `/payments`, as a new entry."""

    charge_day: Optional[int] = Field(default=None, ge=1, le=31)
    # Sent as null to clear it.
    end_date: Optional[date] = None


class MortgagePaymentCreate(BaseModel):
    amount: float
    effective_from: date

    _amount_positive = field_validator("amount")(_positive)


class MortgagePaymentRead(BaseModel):
    id: int
    amount: float
    effective_from: date
    created_at: UtcDatetime

    model_config = ConfigDict(from_attributes=True)


class MortgageRead(BaseModel):
    id: int
    property_id: int
    charge_day: int
    end_date: Optional[date] = None
    # The payment in force on the owner's today; null before the first entry starts and
    # after `end_date`. Set by MortgageService, not stored.
    current_payment: Optional[float] = None
    # Newest first.
    payments: list[MortgagePaymentRead] = []
    created_at: UtcDatetime

    model_config = ConfigDict(from_attributes=True)

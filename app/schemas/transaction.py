from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TransactionType(str, Enum):
    revenue = "revenue"
    expense = "expense"


class PaymentMethod(str, Enum):
    bit = "bit"
    cash = "cash"
    bank_transfer = "bank_transfer"


class TransactionCreateRevenue(BaseModel):
    property_id: int
    renter_id: Optional[int] = None
    amount: float
    date_of_payment: Optional[date] = None
    month_for: date
    payment_method: Optional[PaymentMethod] = None
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: float) -> float:
        if v is not None and v <= 0:
            raise ValueError("amount must be greater than 0")
        return v


class TransactionCreateExpense(BaseModel):
    property_id: int
    renter_id: Optional[int] = None
    amount: float
    date_of_payment: date
    payment_method: PaymentMethod
    category_id: int
    supplier_id: Optional[int] = None
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: float) -> float:
        if v is not None and v <= 0:
            raise ValueError("amount must be greater than 0")
        return v


class TransactionRead(BaseModel):
    id: int
    type: TransactionType
    property_id: int
    renter_id: Optional[int] = None
    payment_method: Optional[PaymentMethod] = None
    date_of_payment: date
    month_for: Optional[date] = None
    amount: Decimal
    currencyCode: str = Field(validation_alias="currency_code")
    category_id: Optional[int] = None
    supplier_id: Optional[int] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    property_name: Optional[str] = None
    renter_name: Optional[str] = None
    category_name: Optional[str] = None
    supplier_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

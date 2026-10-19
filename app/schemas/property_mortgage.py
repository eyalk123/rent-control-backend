from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.stored_file import StoredFilePath
from app.schemas.utc import UtcDatetime


def _positive(v: float) -> float:
    if v <= 0:
        raise ValueError("amount must be greater than 0")
    return v


class MortgageCreate(BaseModel):
    """A mortgage as the property form and `POST /mortgages` send it.

    From the property form it is also the *edit*: there `monthly_payment` corrects the amount
    in force today instead of adding one. A change from a date on is
    `POST /mortgages/{id}/payments`, which keeps the months before it as they were.
    """

    # Principal and interest together — the figure on the bank statement.
    monthly_payment: float
    # When that payment applies from. Omitted means today: the owner is saying what they
    # pay now, and nothing before today is computed from it yet.
    payment_from: Optional[date] = None
    charge_day: int = Field(ge=1, le=31)
    end_date: Optional[date] = None
    # Part of the loan is on the prime track. On unless sent off — see PropertyMortgage.
    has_prime_track: bool = True

    _monthly_payment_positive = field_validator("monthly_payment")(_positive)


class MortgageUpdate(BaseModel):
    """The mortgage's own fields. The payment changes through `/payments`, as a new entry."""

    charge_day: Optional[int] = Field(default=None, ge=1, le=31)
    # Sent as null to clear it.
    end_date: Optional[date] = None
    has_prime_track: Optional[bool] = None


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


class MortgageYearWrite(BaseModel):
    """One year's figures from the bank's yearly statement.

    ``document_url`` is the statement just uploaded, kept as a property file labelled
    ``document_label``. Left out, the year keeps the document it has; null removes it.
    """

    interest: float = Field(ge=0, le=1_000_000_000)
    # How much the debt rose with the index. 0 without a linked track.
    linkage: float = Field(ge=0, le=1_000_000_000)
    balance: Optional[float] = Field(default=None, ge=0, le=10_000_000_000)
    document_url: Optional[StoredFilePath] = None
    document_label: Optional[str] = Field(default=None, max_length=200)


class MortgageDocumentRead(BaseModel):
    id: int
    url: str
    label: str

    model_config = ConfigDict(from_attributes=True)


class MortgageYearRead(BaseModel):
    year: int
    interest: float
    linkage: float
    balance: Optional[float] = None
    document: Optional[MortgageDocumentRead] = None
    updated_at: UtcDatetime

    model_config = ConfigDict(from_attributes=True)


class MortgageRead(BaseModel):
    id: int
    property_id: int
    charge_day: int
    end_date: Optional[date] = None
    has_prime_track: bool = True
    # The payment in force on the owner's today; null before the first entry starts and
    # after `end_date`. Set by MortgageService, not stored.
    current_payment: Optional[float] = None
    # Newest first.
    payments: list[MortgagePaymentRead] = []
    # The bank's yearly summaries, newest year first.
    years: list[MortgageYearRead] = []
    created_at: UtcDatetime

    model_config = ConfigDict(from_attributes=True)

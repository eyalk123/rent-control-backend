from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.device_token import DevicePlatformEnum
from app.models.legal_acceptance import LegalDocumentEnum


class LegalAcceptanceCreate(BaseModel):
    document: LegalDocumentEnum
    # What the client displayed. See the note on LegalAcceptance.version for why the
    # server takes the client's word for this rather than stamping its own constant.
    version: str = Field(min_length=1, max_length=64)
    locale: str = Field(min_length=1, max_length=16)


class LegalAcceptanceRecord(BaseModel):
    """One acceptance gesture, which covers both documents — the UI is a single tick."""

    acceptances: list[LegalAcceptanceCreate] = Field(min_length=1, max_length=8)
    platform: DevicePlatformEnum


class LegalAcceptanceRead(BaseModel):
    document: LegalDocumentEnum
    version: str
    locale: str
    platform: DevicePlatformEnum
    accepted_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LegalStatusRead(BaseModel):
    """The latest acceptance per document, plus the versions this server considers current.

    ``required_*_version`` is reported, not enforced. The clients bundle their own copy of
    the document text, so each one gates on its *own* bundled version: that way nobody is
    ever asked to accept wording their build cannot render. These two fields exist so the
    mismatch is visible — how many people are still on the previous terms because their app
    store build lags the web one is a real question, and without them it is unanswerable.
    """

    terms: LegalAcceptanceRead | None = None
    privacy: LegalAcceptanceRead | None = None
    required_terms_version: str
    required_privacy_version: str

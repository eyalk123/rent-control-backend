"""Request/response shapes for in-app support submissions.

Screenshots arrive as base64 inside the request body rather than as Firebase
Storage URLs. That keeps renter PII out of our infrastructure entirely — see
``app/models/support_message.py`` — and it also means the payload is the only
thing standing between a client and the outgoing email, so every field here is
validated rather than trusted: the declared content type against an allowlist,
the base64 against an actual decode, and the decoded total against a byte cap.
"""
import base64
import binascii
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import settings
from app.models.support_message import SupportMessageTypeEnum

#: What a screenshot is allowed to be. Deliberately images only: this field exists
#: to show us a screen, and widening it to documents would turn a support form into
#: a general-purpose file relay pointed at the product owner's inbox.
ALLOWED_SCREENSHOT_TYPES = ("image/jpeg", "image/png", "image/webp")


class ScreenshotIn(BaseModel):
    """One base64 screenshot on its way to becoming an email attachment."""

    filename: str = Field(min_length=1, max_length=120)
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    data: str = Field(min_length=1)

    @field_validator("filename")
    @classmethod
    def _plain_filename(cls, v: str) -> str:
        """Strip any path the client sent.

        The value ends up as an email attachment's filename, so a `../` or a
        directory component has nothing legitimate to do here.
        """
        return v.replace("\\", "/").rsplit("/", 1)[-1].strip() or "screenshot"

    @field_validator("data")
    @classmethod
    def _decodable(cls, v: str) -> str:
        try:
            base64.b64decode(v, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("screenshot data is not valid base64")
        return v

    @property
    def decoded_size(self) -> int:
        return len(base64.b64decode(self.data, validate=True))


class SupportMessageCreate(BaseModel):
    type: SupportMessageTypeEnum
    message: str = Field(min_length=1, max_length=5000)
    screenshots: list[ScreenshotIn] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def _within_attachment_budget(self) -> "SupportMessageCreate":
        total = sum(shot.decoded_size for shot in self.screenshots)
        if total > settings.SUPPORT_MESSAGE_MAX_ATTACHMENT_BYTES:
            raise ValueError(
                "screenshots exceed the maximum total size "
                f"({settings.SUPPORT_MESSAGE_MAX_ATTACHMENT_BYTES} bytes)"
            )
        return self


class SupportMessageRead(BaseModel):
    """What the submitter gets back. Deliberately thin — there is nothing to list."""

    id: int
    type: SupportMessageTypeEnum
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

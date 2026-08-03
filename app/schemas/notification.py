import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.notification import NotificationTypeEnum


def _as_list(v):
    """Parse a JSON-text list column into a Python list (pass lists through)."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v) or []
        except json.JSONDecodeError:
            return []
    return v


# ── feed ──────────────────────────────────────────────────────────────────

class NotificationRead(BaseModel):
    """An enriched feed item (renter name/address resolved at read time)."""

    id: int
    type: NotificationTypeEnum
    renter_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    property_id: Optional[int] = None
    property_address: Optional[str] = None
    payment_type: Optional[str] = None
    offset: int
    data: dict = {}
    read: bool
    dismissed: bool
    created_at: datetime


# ── settings ────────────────────────────────────────────────────────────────

class NotificationSettingsRead(BaseModel):
    master_enabled: bool
    muted_events: list[str]
    cpi_min_change_amount: float
    cpi_min_change_percent: float

    model_config = ConfigDict(from_attributes=True)

    @field_validator("muted_events", mode="before")
    @classmethod
    def _parse_muted(cls, v):
        return _as_list(v)


class NotificationSettingsUpdate(BaseModel):
    master_enabled: Optional[bool] = None
    muted_events: Optional[list[str]] = None
    cpi_min_change_amount: Optional[float] = Field(default=None, ge=0)
    cpi_min_change_percent: Optional[float] = Field(default=None, ge=0)


# ── rules ─────────────────────────────────────────────────────────────────

class NotificationRuleRead(BaseModel):
    id: int
    event_type: NotificationTypeEnum
    label: Optional[str] = None
    enabled: bool
    offsets: list[int]
    scope_property_ids: list[int]
    scope_property_owners: list[str]
    scope_renter_ids: list[int]

    model_config = ConfigDict(from_attributes=True)

    @field_validator(
        "offsets",
        "scope_property_ids",
        "scope_property_owners",
        "scope_renter_ids",
        mode="before",
    )
    @classmethod
    def _parse_lists(cls, v):
        return _as_list(v)


class NotificationRuleCreate(BaseModel):
    event_type: NotificationTypeEnum
    label: Optional[str] = None
    enabled: bool = True
    offsets: list[int] = []
    scope_property_ids: list[int] = []
    scope_property_owners: list[str] = []
    scope_renter_ids: list[int] = []


class NotificationRuleUpdate(BaseModel):
    # event_type is fixed at creation (a rule belongs to one event section).
    label: Optional[str] = None
    enabled: Optional[bool] = None
    offsets: Optional[list[int]] = None
    scope_property_ids: Optional[list[int]] = None
    scope_property_owners: Optional[list[str]] = None
    scope_renter_ids: Optional[list[int]] = None


# ── preferences bundle + preview ────────────────────────────────────────────

class PreferencesRead(BaseModel):
    settings: NotificationSettingsRead
    rules: list[NotificationRuleRead]


class RulePreviewRequest(BaseModel):
    event_type: NotificationTypeEnum
    offsets: list[int] = []
    scope_property_ids: list[int] = []
    scope_property_owners: list[str] = []
    scope_renter_ids: list[int] = []


class RulePreviewResponse(BaseModel):
    matched_renters: int
    estimated_alerts: int

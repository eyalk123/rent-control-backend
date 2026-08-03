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


def _as_dict(v):
    """Parse a JSON-text object column into a Python dict (pass dicts through)."""
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v) or {}
        except json.JSONDecodeError:
            return {}
    return v


# ── feed ──────────────────────────────────────────────────────────────────

class NotificationRead(BaseModel):
    """An enriched feed item (renter name/address resolved at read time)."""

    id: int
    type: NotificationTypeEnum
    renter_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    # Carried so a client can offer "message this renter" straight from the feed row
    # without a second round-trip per alert.
    phone: Optional[str] = None
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
    # ``{template_key: {locale: text}}`` — overrides only; see the model for why.
    whatsapp_templates: dict[str, dict[str, str]] = {}

    model_config = ConfigDict(from_attributes=True)

    @field_validator("muted_events", mode="before")
    @classmethod
    def _parse_muted(cls, v):
        return _as_list(v)

    @field_validator("whatsapp_templates", mode="before")
    @classmethod
    def _parse_templates(cls, v):
        return _as_dict(v)


class NotificationSettingsUpdate(BaseModel):
    master_enabled: Optional[bool] = None
    muted_events: Optional[list[str]] = None
    cpi_min_change_amount: Optional[float] = Field(default=None, ge=0)
    cpi_min_change_percent: Optional[float] = Field(default=None, ge=0)
    # Sent whole, not patched key-by-key: the client holds the full override map and a
    # reset is the *absence* of a key, which a per-key patch could not express.
    whatsapp_templates: Optional[dict[str, dict[str, str]]] = None


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

"""Schemas for onboarding tour state.

The two maps are kept apart on purpose: `seeds_shown` records that a feature was *named*
somewhere the user could see it, `tours_seen` that it was actually *explained*. A seed
must never consume its destination tour, so they can never collapse into one set.

Values are ISO-8601 timestamps rather than booleans. A bare `true` would answer "has he
seen it"; a timestamp also answers "how long ago" — which is what a later "seeded three
sessions back and still never opened it" nudge would need, without another migration.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_ENTRIES = 200


def _as_timestamp_map(v) -> dict[str, str]:
    """Accepts the stored JSON (or an already-parsed dict) and normalises to {key: iso}."""
    import json

    if v is None or v == "":
        return {}
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except (ValueError, TypeError):
            return {}
    if not isinstance(v, dict):
        return {}
    return {str(k): str(val) for k, val in v.items() if val is not None}


class TourStateRead(BaseModel):
    tours_seen: dict[str, str] = {}
    seeds_shown: dict[str, str] = {}
    tours_disabled: bool = False

    model_config = ConfigDict(from_attributes=True)

    @field_validator("tours_seen", "seeds_shown", mode="before")
    @classmethod
    def _parse(cls, v):
        return _as_timestamp_map(v)


class TourStateUpdate(BaseModel):
    """A patch, not a replacement.

    Clients send only what just happened — `{"tours_seen": {"first-run": "<now>"}}` — and
    the server merges it into what is already stored. Replacement semantics would lose
    writes whenever the phone and the browser are both open, which for this data is the
    normal case rather than the edge case.
    """

    tours_seen: Optional[dict[str, datetime]] = None
    seeds_shown: Optional[dict[str, datetime]] = None
    tours_disabled: Optional[bool] = None
    #: Clears both maps — the "Show tours again" control in Settings. Applied before the
    #: merge, so a reset and a fresh entry can arrive in the same request.
    reset: bool = Field(default=False)

    @field_validator("tours_seen", "seeds_shown")
    @classmethod
    def _bounded(cls, v):
        """A malformed or hostile client must not be able to grow the row without limit."""
        if v is not None and len(v) > MAX_ENTRIES:
            raise ValueError(f"at most {MAX_ENTRIES} entries per request")
        return v

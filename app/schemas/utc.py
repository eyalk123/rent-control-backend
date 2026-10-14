"""A response timestamp that says it is UTC.

The ``DateTime`` columns store naive values that hold UTC (see ``app.clock``), and pydantic
serialises a naive datetime with no offset: ``2026-10-15T21:30:00``. JavaScript reads an
offset-less date-time as the *device's local* time, so every timestamp the clients showed was
off by the user's UTC offset — two or three hours in Israel, and the wrong day near midnight.

Annotating a response field with :data:`UtcDatetime` attaches UTC on the way out, so the wire
form is ``2026-10-15T21:30:00Z`` and every client converts it to local time correctly. Only the
JSON form changes; ``model_dump()`` in Python mode still returns the naive value, so code that
compares it against a column is unaffected.
"""
from datetime import datetime, timezone
from typing import Annotated

from pydantic import PlainSerializer


def as_utc(value: datetime) -> datetime:
    """``value`` as an aware UTC datetime. Naive values are UTC by convention."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


UtcDatetime = Annotated[
    datetime,
    PlainSerializer(as_utc, return_type=datetime, when_used="json"),
]

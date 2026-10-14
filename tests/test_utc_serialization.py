"""Response timestamps must say they are UTC, or JavaScript reads them as local time."""
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from app.schemas.utc import UtcDatetime, as_utc


class _Model(BaseModel):
    at: UtcDatetime
    maybe: UtcDatetime | None = None


def test_naive_value_is_sent_as_utc():
    dumped = _Model(at=datetime(2026, 10, 15, 21, 30)).model_dump_json()
    assert '"at":"2026-10-15T21:30:00Z"' in dumped


def test_aware_value_is_converted_to_utc():
    jerusalem = timezone(timedelta(hours=3))
    dumped = _Model(at=datetime(2026, 10, 16, 0, 30, tzinfo=jerusalem)).model_dump_json()
    assert '"at":"2026-10-15T21:30:00Z"' in dumped


def test_python_mode_keeps_the_naive_value():
    # Code that compares a dumped value against a naive column must not get an aware one.
    assert _Model(at=datetime(2026, 10, 15, 21, 30)).model_dump()["at"].tzinfo is None


def test_none_stays_none():
    assert '"maybe":null' in _Model(at=datetime(2026, 1, 1)).model_dump_json()


def test_as_utc_marks_naive_values():
    assert as_utc(datetime(2026, 1, 1)).tzinfo is timezone.utc

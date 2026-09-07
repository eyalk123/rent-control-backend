"""One clock for the whole backend, and it reads UTC.

Two things went wrong before this module existed, and both are the same mistake wearing
different clothes:

* ``datetime.utcnow()`` returns a *naive* datetime that happens to hold UTC. Nothing about
  the value says so, so it compares happily against a local-clock value and silently gives
  the wrong answer. It is also deprecated as of Python 3.12.
* ``date.today()`` is the local calendar date. On Railway the container runs UTC so it
  agreed with the stored timestamps by accident; on a developer's machine in Israel it is
  ahead of UTC for the first three hours of every day, which is exactly when "has today's
  job already run?" started answering no to a run that had happened.

So: every current time in this codebase comes from here, and the timezone is never the
host's business.

**Why the stored form is naive.** The ``DateTime`` columns are naive (``TIMESTAMP WITHOUT
TIME ZONE``), and values read back out of them are naive too — so a timezone-aware "now"
cannot be subtracted from a stored timestamp without raising ``TypeError``. Rather than
migrate every timestamp column to ``timezone=True``, the awareness is used where it
belongs — at the source, ``datetime.now(timezone.utc)`` — and dropped at the storage
boundary by :func:`utc_now_naive`, which is the only place that is allowed to do it. The
value is UTC either way; what differs is whether the tzinfo travels with it.

Use :func:`utc_now` for anything that stays in Python, :func:`utc_now_naive` for anything
written to or compared against a column, and :func:`utc_today` for "which UTC day is it".

Business dates are a different question and do **not** belong here: a lease that starts
today, a payment dated today and a report's date range are the *user's* calendar, not the
server's, and those keep using ``date.today()``.
"""
from datetime import date, datetime, timezone


def utc_now() -> datetime:
    """Timezone-aware current time in UTC."""
    return datetime.now(timezone.utc)


def utc_now_naive() -> datetime:
    """Current UTC time in the form the ``DateTime`` columns store: naive, but UTC.

    The tzinfo is dropped deliberately and only here — see the module docstring. Passed
    as a callable to ``default=``/``onupdate=`` so it is evaluated per insert, which is
    also what lets freezegun-based tests control the value.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_today() -> date:
    """Today's date on the UTC calendar.

    For comparing against stored timestamps, which are UTC. Not for business dates.
    """
    return datetime.now(timezone.utc).date()

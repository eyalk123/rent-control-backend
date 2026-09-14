"""Count what each owner does in each client, in memory, and flush it periodically.

Fills in ``owner_client_days`` — read that model's docstring first for *why* the table
exists and why it carries two counters rather than a presence flag.

**Why an accumulator rather than a write per request.** The table holds counts, so the
cheap "write the row once a day and skip afterwards" trick is not available: every request
has to be counted. Writing a row per request would put a database round-trip on the hot
path of every authenticated call in the product, to record telemetry. So requests
increment a process-local dict and touch no database at all; a background task drains the
dict into Postgres once a minute, and again on shutdown.

Six properties of that design are load-bearing:

1. **The day is computed when the request happens, not when the flush happens.** It is
   part of the dict key, so a flush that straddles midnight UTC still files each request
   under the day it occurred. Deriving the day at flush time would silently misfile
   everything in the window that crosses midnight.
2. **The upsert adds, it does not set.** Railway may run more than one worker, each with
   its own accumulator, both flushing to the same row. ``DO NOTHING`` or a plain ``SET``
   would drop one worker's counts entirely — see :func:`_upsert`.
3. **The drain is atomic.** The dict is swapped out and replaced before anything is
   written, so requests arriving mid-flush accumulate into the fresh dict rather than
   being double-counted or dropped. If the write then fails the drained counts are merged
   back rather than discarded.
4. **It flushes on a timer and on shutdown.** Railway sends SIGTERM with a drain period,
   so the shutdown flush normally completes. A hard kill loses at most one interval of
   counts — acceptable for telemetry, and stated here so nobody has to rediscover it.
5. **It never breaks a request.** Both the increment and the flush swallow everything, the
   same way the owner-profile upsert in ``get_current_owner`` does. A telemetry failure
   must not turn a working request into a 500.
6. **Memory is bounded.** The dict is keyed by owners active right now, so it is naturally
   small, but an unbounded dict in a long-lived process is a slow leak: past
   :data:`MAX_KEYS` it forces an early flush, and a failed flush drops keys for days that
   are no longer current rather than carrying them forever.

Header values are client-supplied and are never trusted into the database verbatim —
anything outside the known sets becomes ``"unknown"``, and the version string is length-
and charset-checked.
"""
import asyncio
import logging
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.clock import utc_now_naive, utc_today
from app.database import SessionLocal
from app.models.owner_client_day import OwnerClientDay

logger = logging.getLogger(__name__)

# --- Header vocabulary -------------------------------------------------------------

CLIENT_APP_HEADER = "x-client-app"
CLIENT_PLATFORM_HEADER = "x-client-platform"
CLIENT_VERSION_HEADER = "x-client-version"

#: The three headers, in the spelling a browser sends them, for the CORS allowlist.
CLIENT_HEADERS = ("X-Client-App", "X-Client-Platform", "X-Client-Version")

UNKNOWN = "unknown"
KNOWN_APPS = frozenset({"mobile", "web"})
KNOWN_PLATFORMS = frozenset({"ios", "android", "web"})

#: Semver, a build number or a git sha — nothing else gets stored. The cap is on both
#: length and charset because the value arrives from a client and lands in a column.
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,31}$")

# --- What counts as work -----------------------------------------------------------

_MUTATING = ("POST", "PATCH", "PUT", "DELETE")

#: An allowlist of ``(methods, path prefix)`` pairs, deliberately *not* "any POST/PATCH/
#: DELETE". Two things in this codebase break a method-based rule, and both break it in
#: the direction that destroys the metric:
#:
#: * Housekeeping calls fire automatically on app open. ``POST /device-tokens`` (push
#:   registration), ``POST /users/me/legal`` (the consent gate) and
#:   ``PATCH /users/me/tour-state`` (onboarding) all happen without the user doing
#:   anything, so counting them makes every five-second app open look like work — which is
#:   exactly the failure this table exists to avoid.
#: * Report exports are ``GET``. ``GET /reports/income-expense`` and
#:   ``GET /reports/expense-log`` generate a file and write a ``ReportExport`` row.
#:   Exporting a report is real work, and a method-based rule misses it entirely.
#:
#: Being an allowlist is the point: a housekeeping endpoint added later is *ignored* until
#: somebody opts it in, rather than silently inflating the metric. That is the safe
#: direction to fail in.
WRITE_ENDPOINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (_MUTATING, "/properties"),
    (_MUTATING, "/renters"),
    (_MUTATING, "/transactions"),
    (_MUTATING, "/suppliers"),
    (_MUTATING, "/expense-categories"),
    # Lease scanning: the upload, and the PATCH that records what the owner did with it.
    (("POST", "PATCH"), "/extract"),
    # Sending a message to the assistant. GET /agent/status and the conversation list are
    # reading, and DELETE of a conversation is housekeeping.
    (("POST", "PATCH"), "/agent"),
    # The two exports — GET, and real work. See the note above.
    (("GET",), "/reports/income-expense"),
    (("GET",), "/reports/expense-log"),
    (("DELETE",), "/reports/history"),
)

# Everything else is reading or housekeeping and counts towards `requests` only:
# /device-tokens (fires on every app launch), /users/me/legal, /users/me/tour-state and
# /users/me/country (consent and onboarding, automatic), /notifications and notification
# preferences (marking read is not work), every GET not named above, and /internal/* (cron,
# which has no owner to attribute anyway).

# --- Accumulator tuning ------------------------------------------------------------

#: Seconds between background flushes. A hard kill loses at most this much telemetry.
FLUSH_INTERVAL_SECONDS = 60
#: Past this many live keys, flush immediately instead of waiting for the timer.
MAX_KEYS = 5_000
#: On a failed flush, counts for days older than this are dropped rather than retried
#: forever. Two days is generous: a flush is a minute's work, not a day's.
STALE_DAYS = 2


def _normalize(value: str | None, allowed: frozenset[str]) -> str:
    if not value:
        return UNKNOWN
    candidate = value.strip().lower()
    return candidate if candidate in allowed else UNKNOWN


def _normalize_version(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    return candidate if _VERSION_RE.match(candidate) else None


def _matches_prefix(path: str, prefix: str) -> bool:
    """Segment-aware prefix match: ``/properties`` covers ``/properties/42`` but not
    ``/properties-archive``."""
    return path == prefix or path.startswith(prefix + "/")


def is_write(method: str, path: str) -> bool:
    method = method.upper()
    return any(
        method in methods and _matches_prefix(path, prefix)
        for methods, prefix in WRITE_ENDPOINTS
    )


@dataclass
class _Counts:
    first_seen_at: datetime
    last_seen_at: datetime
    requests: int = 0
    writes: int = 0
    app_version: str | None = None

    def merge(self, other: "_Counts") -> None:
        self.requests += other.requests
        self.writes += other.writes
        self.first_seen_at = min(self.first_seen_at, other.first_seen_at)
        self.last_seen_at = max(self.last_seen_at, other.last_seen_at)
        # Last version seen wins, but a request without the header must not erase one.
        self.app_version = other.app_version or self.app_version


_Key = tuple[str, date, str, str]


class ClientUsageRecorder:
    """Process-local counters for ``owner_client_days``.

    One instance per process (:data:`recorder` below). The lock is cheap and removes any
    need to reason about which thread a given increment ran on: the middleware increments
    on the event loop, while a flush does its database work in a worker thread.
    """

    def __init__(self, session_factory=SessionLocal):
        self.session_factory = session_factory
        self._counts: dict[_Key, _Counts] = {}
        self._lock = threading.Lock()
        # Set when the dict is over MAX_KEYS, so the flush loop stops waiting out its
        # interval and drains early.
        self._flush_now = asyncio.Event()

    # -- capture ---------------------------------------------------------------

    def record(
        self,
        *,
        owner_id: str,
        method: str,
        path: str,
        app: str | None,
        platform: str | None,
        app_version: str | None,
        counted_as_write: bool,
    ) -> None:
        """Increment one key. Pure memory — no database, no I/O."""
        now = utc_now_naive()
        key: _Key = (
            owner_id,
            # Resolved here, not at flush time: see the module docstring, point 1.
            utc_today(),
            _normalize(app, KNOWN_APPS),
            _normalize(platform, KNOWN_PLATFORMS),
        )
        version = _normalize_version(app_version)

        with self._lock:
            entry = self._counts.get(key)
            if entry is None:
                entry = _Counts(first_seen_at=now, last_seen_at=now)
                self._counts[key] = entry
            entry.requests += 1
            if counted_as_write:
                entry.writes += 1
            entry.last_seen_at = now
            if version:
                entry.app_version = version
            over_cap = len(self._counts) > MAX_KEYS

        if over_cap:
            self._flush_now.set()

    # -- flush -----------------------------------------------------------------

    def _drain(self) -> dict[_Key, _Counts]:
        """Swap the dict out and replace it, so nothing arriving mid-flush is lost."""
        with self._lock:
            drained, self._counts = self._counts, {}
        return drained

    def _restore(self, drained: dict[_Key, _Counts]) -> None:
        """Put failed counts back, minus anything too old to be worth retrying."""
        cutoff = utc_today() - timedelta(days=STALE_DAYS)
        with self._lock:
            for key, counts in drained.items():
                if key[1] < cutoff:
                    continue
                existing = self._counts.get(key)
                if existing is None:
                    self._counts[key] = counts
                else:
                    existing.merge(counts)

    def flush(self, db: Session | None = None) -> int:
        """Write the accumulated counts. Returns the number of keys written.

        Best-effort: every failure is swallowed and the counts are merged back for the
        next attempt, because this runs on the shutdown path and from a background task
        where an exception has nowhere useful to go.
        """
        drained = self._drain()
        if not drained:
            return 0

        # `db` is passed only by tests, which own the session's lifetime; the background
        # task and the shutdown hook get their own and close it.
        owns_session = db is None
        session = self.session_factory() if owns_session else db
        try:
            _upsert(session, drained)
            session.commit()
        except Exception as exc:  # noqa: BLE001 — telemetry must never propagate
            # Rolled back so a caller-supplied session is still usable afterwards: a
            # failure here must cost the telemetry, not the session it borrowed.
            try:
                session.rollback()
            except Exception:
                pass
            logger.debug("Client usage flush failed (%d keys retained): %s", len(drained), exc)
            self._restore(drained)
            return 0
        finally:
            if owns_session:
                session.close()
        return len(drained)

    async def run_flush_loop(self) -> None:
        """Flush every :data:`FLUSH_INTERVAL_SECONDS`, or sooner if the dict is over cap.

        Started by the FastAPI lifespan; cancelled on shutdown, which then does one final
        flush. The database work goes to a worker thread so the event loop keeps serving.
        """
        while True:
            try:
                await asyncio.wait_for(
                    self._flush_now.wait(), timeout=FLUSH_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass
            # Cleared here rather than inside the drain because an asyncio.Event is only
            # safe to touch from the loop thread, and the drain itself runs in a worker.
            # A request arriving during the flush re-sets it and earns another pass.
            self._flush_now.clear()
            await asyncio.to_thread(self.flush)


def _upsert(db: Session, drained: dict[_Key, _Counts]) -> None:
    """One ``INSERT ... ON CONFLICT DO UPDATE`` that **adds** the drained counts.

    Adding rather than setting is what makes the flush safe when more than one worker is
    running: each holds its own accumulator and they flush independently to the same row.
    ``DO NOTHING`` would drop the second worker's counts; ``SET`` would drop the first's.

    Each key appears exactly once in the drained dict, so the multi-row VALUES can never
    hit the same conflict target twice — which PostgreSQL rejects outright.
    """
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert

        newest = func.greatest
    else:
        # SQLite (the test suite). Same ON CONFLICT syntax; GREATEST is spelled MAX.
        from sqlalchemy.dialects.sqlite import insert

        newest = func.max

    rows = [
        {
            "owner_id": owner_id,
            "day": day,
            "app": app,
            "platform": platform,
            "app_version": counts.app_version,
            "requests": counts.requests,
            "writes": counts.writes,
            "first_seen_at": counts.first_seen_at,
            "last_seen_at": counts.last_seen_at,
        }
        for (owner_id, day, app, platform), counts in drained.items()
    ]

    table = OwnerClientDay.__table__
    stmt = insert(table).values(rows)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["owner_id", "day", "app", "platform"],
            set_={
                "requests": table.c.requests + stmt.excluded.requests,
                "writes": table.c.writes + stmt.excluded.writes,
                "last_seen_at": newest(table.c.last_seen_at, stmt.excluded.last_seen_at),
                # COALESCE, not a bare assignment: a client that sends the app and
                # platform headers but no version must not blank out the version other
                # requests recorded for the same row.
                "app_version": func.coalesce(stmt.excluded.app_version, table.c.app_version),
            },
        )
    )


#: The process-wide accumulator. Imported by the middleware and by the lifespan hook.
recorder = ClientUsageRecorder()

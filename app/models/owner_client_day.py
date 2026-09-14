"""How much each owner did in each client, per day.

The product ships two clients — an Expo mobile app and a React web app — against one
backend, and until this table existed nothing said which one an owner actually used.
Both clients sent identical headers, so every request looked the same to the server.
``legal_acceptances.platform`` records where someone *signed up* and
``device_tokens.platform`` records where push was registered; neither says where the work
happens day to day. That is the gap this fills, and the reason is that engineering effort
has to be split between the two clients and there was no evidence to split it on.

**Why per owner-day rather than per record.** Attributing each row would mean a nullable
column on five tables, five migrations, and a value that is NULL for everything written
before it. Worse, it cannot see *reading*: an owner who reviews reports on a laptop and
saves nothing would leave no trace at all, and that is exactly the behaviour worth
knowing about. One row per owner per client per day is bounded (owners x clients x days)
and answers the question directly.

**Why two counters.** Mere presence is useless. An owner who opens the web app for five
seconds and one who works in it all evening would otherwise produce identical rows, and
on any day someone touches both clients you could not say which they actually worked in.
``requests`` measures attention; ``writes`` measures work. A glance is
``requests=3, writes=0``; an evening of recording payments is ``requests=140, writes=22``.
Any "where does the work happen" chart is built on ``writes`` — never on row counts, and
never on ``requests``.

What counts as a write is an explicit allowlist, not the HTTP method — see
``app/services/client_usage_service.py``, which explains why the method alone gets it
wrong in both directions.

**Why ``app`` and ``platform`` are separate.** The mobile app can run in a browser via the
Expo web preview, where ``Platform.OS === 'web'``. A single column would merge that with
the real web app and destroy the one distinction this table exists to measure.

Both are plain strings rather than a PostgreSQL enum, for the reason ``PropertyTypeEnum``
records: an enum value cannot be dropped without rewriting the type and every column using
it, and this vocabulary will change as clients come and go. A missing or unrecognised
header is stored as ``"unknown"`` rather than NULL, so counting never has to special-case
it — old clients that never send the headers are a real and permanent case.
"""
from sqlalchemy import Column, Date, DateTime, Index, Integer, String, UniqueConstraint

from app.clock import utc_now_naive
from app.models.base import Base


class OwnerClientDay(Base):
    __tablename__ = "owner_client_days"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False)
    # The UTC day, from app.clock.utc_today() — the same calendar the timestamps use.
    day = Column(Date, nullable=False)
    app = Column(String, nullable=False)  # mobile | web | unknown
    platform = Column(String, nullable=False)  # ios | android | web | unknown
    app_version = Column(String, nullable=True)
    # Every authenticated request the owner made from this client on this day.
    requests = Column(Integer, nullable=False, default=0, server_default="0")
    # The subset of those that did real work (the allowlist in client_usage_service).
    writes = Column(Integer, nullable=False, default=0, server_default="0")
    first_seen_at = Column(DateTime, nullable=False, default=utc_now_naive)
    last_seen_at = Column(DateTime, nullable=False, default=utc_now_naive)

    __table_args__ = (
        # Load-bearing, not just hygiene: it is what makes the counter flush an idempotent
        # upsert (ON CONFLICT ... DO UPDATE SET requests = requests + EXCLUDED.requests),
        # which is how two workers flushing their own in-memory accumulators end up adding
        # to one row instead of one silently overwriting the other.
        UniqueConstraint(
            "owner_id", "day", "app", "platform", name="uq_owner_client_days_key"
        ),
        # Every dashboard query is "the last N days", so the time window is the filter.
        Index("ix_owner_client_days_day", "day"),
    )

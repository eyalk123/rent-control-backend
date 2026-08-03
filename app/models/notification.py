import enum
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.models.base import Base


class NotificationTypeEnum(str, enum.Enum):
    OVERDUE = "overdue"
    LEASE_EXPIRING = "lease_expiring"
    CPI_RENT_CHANGE = "cpi_rent_change"


class Notification(Base):
    """A single generated notification — the source of both the in-app feed and
    the push that was (or will be) sent for it.

    One row per logical notification. ``period_key`` + ``offset`` make
    "once per reminder" concrete: the current ``YYYY-MM`` for overdue rent, the
    ISO ``lease_end`` date for an expiring lease, or the ISO anniversary of the
    lease year being repriced for a CPI rent change — paired with the rule offset
    so a 90-day and a 30-day reminder for the same lease are distinct rows.

    ``cpi_rent_change`` reuses that key for its two stages rather than needing a
    second type: offset ``30`` is the heads-up before the anniversary and offset
    ``0`` the confirmation once the amount is set.

    ``sent_at`` is when the row was generated; ``pushed_at`` records the push
    delivery attempt; ``read_at`` / ``dismissed_at`` track in-app state.
    """

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    type = Column(
        Enum(NotificationTypeEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    entity_id = Column(Integer, nullable=False)  # renter id
    period_key = Column(String, nullable=False)
    offset = Column(Integer, nullable=False, default=0)
    data = Column(Text, nullable=True)  # JSON: days_overdue / days_until_expiry, amount, offset
    # Call-time lambda (not a bare ``datetime.utcnow`` reference) so the default is
    # re-evaluated on each insert — this lets freezegun-based tests control ``sent_at``.
    sent_at = Column(DateTime, nullable=False, default=lambda: datetime.utcnow())
    pushed_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True)
    dismissed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "type", "entity_id", "period_key", "offset",
            name="uq_notification_dedup",
        ),
    )

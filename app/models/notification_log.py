import enum
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, Integer, String, UniqueConstraint

from app.models.base import Base


class NotificationTypeEnum(str, enum.Enum):
    OVERDUE = "overdue"
    LEASE_EXPIRING = "lease_expiring"


class NotificationLog(Base):
    """Records each reminder already sent so the daily job nudges once per period.

    ``period_key`` makes "once per period" concrete: the current ``YYYY-MM`` for
    overdue rent (one nudge per renter per month) or the ISO ``lease_end`` date for
    an expiring lease (one nudge per lease term).
    """

    __tablename__ = "notification_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False)
    type = Column(
        Enum(NotificationTypeEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    entity_id = Column(Integer, nullable=False)  # renter id
    period_key = Column(String, nullable=False)
    sent_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "type", "entity_id", "period_key",
            name="uq_notification_log_dedup",
        ),
    )

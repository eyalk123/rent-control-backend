from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, String, Text

from app.models.base import Base
from app.models.notification import NotificationTypeEnum


class NotificationRule(Base):
    """A user-composed notification rule: an event + lead-time offsets + scope.

    Rules are additive (union) — a renter triggers a notification if any enabled
    rule matches it; the shared dedup on ``Notification`` collapses overlaps.

    ``offsets`` is a JSON array of non-negative ints; the event type decides
    whether they mean days *before* (lease_expiring) or *after* (overdue) the
    anchor date. All three scope arrays empty ⇒ all properties.
    """

    __tablename__ = "notification_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    event_type = Column(
        Enum(NotificationTypeEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    label = Column(String, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    offsets = Column(Text, nullable=False, default="[]")  # JSON array of ints
    scope_property_ids = Column(Text, nullable=False, default="[]")  # JSON array of ints
    scope_property_owners = Column(Text, nullable=False, default="[]")  # JSON array of strings
    scope_renter_ids = Column(Text, nullable=False, default="[]")  # JSON array of ints
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

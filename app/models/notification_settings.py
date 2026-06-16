from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String, Text

from app.models.base import Base


class NotificationSettings(Base):
    """Per-user, account-wide notification settings that sit above every rule.

    ``muted_events`` is a JSON array of event-type values (e.g. ``["overdue"]``)
    the user has fully opted out of — it suppresses both their custom rules and
    the built-in default for that event.
    """

    __tablename__ = "notification_settings"

    owner_id = Column(String, primary_key=True)
    master_enabled = Column(Boolean, nullable=False, default=True)
    push_enabled = Column(Boolean, nullable=False, default=True)
    inapp_enabled = Column(Boolean, nullable=False, default=True)
    muted_events = Column(Text, nullable=False, default="[]")  # JSON array of event-type strings
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

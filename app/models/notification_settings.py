from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, String, Text

from app.models.base import Base

# A CPI change smaller than *both* of these is index noise, not news. Two floors
# because a single one can't serve a ₪2,000 room and a ₪20,000 commercial unit:
# the absolute one stops trivial shekel movements on cheap units, the relative one
# stops a proportionally meaningless change on expensive ones.
DEFAULT_CPI_MIN_CHANGE_AMOUNT = 10.0
DEFAULT_CPI_MIN_CHANGE_PERCENT = 0.5


class NotificationSettings(Base):
    """Per-user, account-wide notification settings that sit above every rule.

    ``muted_events`` is a JSON array of event-type values (e.g. ``["overdue"]``)
    the user has fully opted out of — it suppresses both their custom rules and
    the built-in default for that event.
    """

    __tablename__ = "notification_settings"

    owner_id = Column(String, primary_key=True)
    master_enabled = Column(Boolean, nullable=False, default=True)
    muted_events = Column(Text, nullable=False, default="[]")  # JSON array of event-type strings
    # Materiality floor for `cpi_rent_change`. Unlike the other events, whose cadence
    # the user tunes with rule offsets, a CPI change fires when the index says so — so
    # the only useful dial is how big a change has to be to be worth hearing about.
    cpi_min_change_amount = Column(
        Float, nullable=False, default=DEFAULT_CPI_MIN_CHANGE_AMOUNT
    )
    cpi_min_change_percent = Column(
        Float, nullable=False, default=DEFAULT_CPI_MIN_CHANGE_PERCENT
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

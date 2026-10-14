from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer
from sqlalchemy.orm import relationship

from app.clock import utc_now_naive
from app.models.base import Base


class PropertyValuation(Base):
    """What the owner thinks the property is worth, as of a date.

    A history rather than one column on the property: a value without a date goes stale
    silently, and keeping each estimate is what lets the apps show how the value moved.
    The property's *current* value is simply the entry with the latest `valued_on`.
    """

    __tablename__ = "property_valuations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(
        Integer, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Stored exactly as typed, in the property's currency — never converted.
    value = Column(Float, nullable=False)
    valued_on = Column(Date, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

    property = relationship("Property", back_populates="valuations")

from datetime import date

from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.models.base import Base


class Renter(Base):
    __tablename__ = "renters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="SET NULL"), nullable=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, nullable=False)
    monthly_rent = Column(Float, nullable=False)
    lease_start = Column(Date, nullable=False)
    lease_end = Column(Date, nullable=False)

    property = relationship("Property", back_populates="renters")

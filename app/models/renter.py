from datetime import date

from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String, Text
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
    lease_years = Column(Text, nullable=False)  # JSON array of {amount, type}
    lease_start = Column(Date, nullable=False)
    lease_end = Column(Date, nullable=False)  # computed internally from lease_start + len(lease_years)
    number_of_payments = Column(Integer, nullable=True)
    payment_type = Column(String, nullable=True)
    payment_day_of_month = Column(Integer, nullable=True)
    insurance_type = Column(String, nullable=True)
    insurance_amount = Column(Float, nullable=True)
    contact_id = Column(String(255), nullable=True)

    property = relationship("Property", back_populates="renters")
    transactions = relationship("Transaction", back_populates="renter")

from datetime import date, datetime

from sqlalchemy import JSON, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import Base


class Renter(Base):
    __tablename__ = "renters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="SET NULL"), nullable=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, nullable=True)
    lease_years = Column(Text, nullable=False)  # JSON array of {amount, type}
    lease_start = Column(Date, nullable=True)
    lease_end = Column(Date, nullable=True)  # computed internally from lease_start + len(lease_years)
    # Structured lease-term intent (the form's higher-level inputs). lease_years
    # stays the source of truth for rent math; these let an edit re-open with the
    # same controls (term length, renewal options, escalation rule).
    contract_term_years = Column(Integer, nullable=True)
    option_years = Column(Integer, nullable=True)
    base_rent = Column(Float, nullable=True)
    rent_escalation_mode = Column(String, nullable=True)  # none | percent | fixed | custom | cpi
    rent_escalation_value = Column(Float, nullable=True)
    # Base CPI index frozen at signing (mode == "cpi" only). Rent for each lease
    # year is base_rent * max(known_index / cpi_base_index, 1). Server-set.
    cpi_base_index = Column(Float, nullable=True)
    # Early exit. Set only via the terminate endpoint, never as a side effect of an
    # edit. It records the lease ending *alongside* the signed terms rather than
    # rewriting them: lease_years, cpi_base_index and the renter's transactions are
    # deliberately untouched, so past reports still reconstruct. NULL == running.
    terminated_on = Column(Date, nullable=True)
    termination_reason = Column(String, nullable=True)
    number_of_payments = Column(Integer, nullable=True)
    payment_type = Column(String, nullable=True)
    payment_day_of_month = Column(Integer, nullable=True)
    insurance_type = Column(String, nullable=True)
    insurance_amount = Column(Float, nullable=True)
    contact_id = Column(String(255), nullable=True)
    extra_contacts = Column(JSON, nullable=True)
    full_contract_url = Column(String, nullable=True)
    id_image_url = Column(String, nullable=True)
    owner_id = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    property = relationship("Property", back_populates="renters")
    transactions = relationship("Transaction", back_populates="renter", passive_deletes=True)

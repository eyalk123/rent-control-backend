from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer
from sqlalchemy.orm import relationship

from app.clock import utc_now_naive
from app.models.base import Base


class PropertyMortgage(Base):
    """A mortgage on one property, as the owner knows it: what goes to the bank each month.

    Deliberately not the loan's structure (tracks, rates, principal). Those are 20-35 fields
    the owner does not know offhand, and a payment computed from them drifts from the bank's
    — a wrong number shown as right is worse than none. The bank's own figure, typed in, is
    the one thing that is always correct.

    One property per mortgage. A loan registered on several properties needs a rule for
    splitting it, which has not been decided; a property may carry several mortgages.
    """

    __tablename__ = "property_mortgages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(
        Integer, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Day of the month the bank collects, 1-31. A 31 in a shorter month is that month's last day.
    charge_day = Column(Integer, nullable=False)
    # Optional: when the mortgage is paid off. From the day after, it no longer counts.
    end_date = Column(Date, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)

    property = relationship("Property", back_populates="mortgages")
    # Newest first, like the value history.
    payments = relationship(
        "MortgagePayment",
        back_populates="mortgage",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="(MortgagePayment.effective_from.desc(), MortgagePayment.id.desc())",
    )

    def payment_on(self, day) -> float | None:
        """The monthly payment in force on `day`: the latest entry that had started by then.

        None before the first entry and after `end_date` — a mortgage not yet running, or
        already paid off, costs nothing that month.
        """
        if self.end_date is not None and day > self.end_date:
            return None
        for payment in self.payments:
            if payment.effective_from <= day:
                return payment.amount
        return None


class MortgagePayment(Base):
    """The monthly payment, from a date on.

    A history rather than one number: after a rate change the owner types the new amount,
    and every month before it keeps the amount that was actually paid then.
    """

    __tablename__ = "mortgage_payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mortgage_id = Column(
        Integer, ForeignKey("property_mortgages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Principal and interest together, as typed, in the property's currency — never converted.
    amount = Column(Float, nullable=False)
    effective_from = Column(Date, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

    mortgage = relationship("PropertyMortgage", back_populates="payments")

import enum
from datetime import date, datetime

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship

from app.models.base import Base


class TransactionTypeEnum(str, enum.Enum):
    REVENUE = "revenue"
    EXPENSE = "expense"


class PaymentMethodEnum(str, enum.Enum):
    BIT = "bit"
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(
        Enum(TransactionTypeEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    renter_id = Column(Integer, ForeignKey("renters.id", ondelete="SET NULL"), nullable=True)
    payment_method = Column(
        Enum(PaymentMethodEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=True,
    )
    date_of_payment = Column(Date, nullable=False)
    month_for = Column(Date, nullable=True)
    amount = Column(Numeric(precision=12, scale=2), nullable=False)
    currency_code = Column(String, nullable=False)
    category_id = Column(
        Integer,
        ForeignKey("expense_categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    supplier_id = Column(
        Integer,
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    property = relationship("Property", back_populates="transactions")
    renter = relationship("Renter", back_populates="transactions")
    category = relationship("ExpenseCategory", back_populates="transactions")
    supplier = relationship("Supplier", back_populates="transactions")

    __table_args__ = (
        Index("ix_transactions_type", "type"),
        Index("ix_transactions_property_id", "property_id"),
        Index("ix_transactions_renter_id", "renter_id"),
        Index("ix_transactions_date_of_payment", "date_of_payment"),
        Index("ix_transactions_property_type_date", "property_id", "type", "date_of_payment"),
    )

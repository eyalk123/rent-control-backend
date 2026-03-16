from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import Base


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category_id = Column(Integer, ForeignKey("expense_categories.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

    category = relationship("ExpenseCategory", back_populates="suppliers")
    transactions = relationship("Transaction", back_populates="supplier")

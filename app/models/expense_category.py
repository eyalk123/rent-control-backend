from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.orm import relationship

from app.models.base import Base


class ExpenseCategory(Base):
    __tablename__ = "expense_categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    suppliers = relationship("Supplier", back_populates="category")
    transactions = relationship("Transaction", back_populates="category")

from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.orm import relationship

from app.models.base import Base


class ExpenseCategory(Base):
    __tablename__ = "expense_categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(255), unique=True, nullable=True)
    name = Column(String(255), nullable=True)
    owner_id = Column(Integer, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    transactions = relationship("Transaction", back_populates="category")
    suppliers = relationship(
        "Supplier",
        secondary="supplier_categories",
        back_populates="categories",
    )

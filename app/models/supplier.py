from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import relationship

from app.models.base import Base

supplier_categories = Table(
    "supplier_categories",
    Base.metadata,
    Column("supplier_id", Integer, ForeignKey("suppliers.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", Integer, ForeignKey("expense_categories.id", ondelete="CASCADE"), primary_key=True),
)


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

    categories = relationship(
        "ExpenseCategory",
        secondary=supplier_categories,
        back_populates="suppliers",
    )
    transactions = relationship("Transaction", back_populates="supplier")

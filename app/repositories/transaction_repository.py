from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.expense_category import ExpenseCategory
from app.models.property import Property
from app.models.renter import Renter
from app.models.supplier import Supplier
from app.models.transaction import Transaction, TransactionTypeEnum


class TransactionRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, transaction: Transaction) -> Transaction:
        self.session.add(transaction)
        self.session.commit()
        self.session.refresh(transaction)
        return transaction

    def get_by_id(
        self,
        transaction_id: int,
        owner_id: int,
    ) -> Transaction | None:
        stmt = (
            select(Transaction)
            .join(Property, Transaction.property_id == Property.id)
            .where(
                Transaction.id == transaction_id,
                Property.owner_id == owner_id,
            )
            .options(
                selectinload(Transaction.property),
                selectinload(Transaction.renter),
                selectinload(Transaction.category),
                selectinload(Transaction.supplier),
            )
        )
        return self.session.scalar(stmt)

    def list(
        self,
        owner_id: int,
        type_filter: TransactionTypeEnum | None = None,
        property_id: int | None = None,
        renter_id: int | None = None,
        q: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Transaction]:
        stmt = (
            select(Transaction)
            .join(Property, Transaction.property_id == Property.id)
            .outerjoin(Renter, Transaction.renter_id == Renter.id)
            .outerjoin(ExpenseCategory, Transaction.category_id == ExpenseCategory.id)
            .outerjoin(Supplier, Transaction.supplier_id == Supplier.id)
            .where(Property.owner_id == owner_id)
            .options(
                selectinload(Transaction.property),
                selectinload(Transaction.renter),
                selectinload(Transaction.category),
                selectinload(Transaction.supplier),
            )
        )
        if type_filter is not None:
            stmt = stmt.where(Transaction.type == type_filter)
        if property_id is not None:
            stmt = stmt.where(Transaction.property_id == property_id)
        if renter_id is not None:
            stmt = stmt.where(Transaction.renter_id == renter_id)
        if from_date is not None:
            stmt = stmt.where(Transaction.date_of_payment >= from_date)
        if to_date is not None:
            stmt = stmt.where(Transaction.date_of_payment <= to_date)
        if q and q.strip():
            search = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    Renter.first_name.ilike(search),
                    Renter.last_name.ilike(search),
                    Property.address.ilike(search),
                    Property.city.ilike(search),
                    Property.property_owner.ilike(search),
                    ExpenseCategory.key.ilike(search),
                    Supplier.name.ilike(search),
                    Transaction.notes.ilike(search),
                )
            )
        stmt = stmt.order_by(
            Transaction.date_of_payment.desc(),
            Transaction.created_at.desc(),
        ).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

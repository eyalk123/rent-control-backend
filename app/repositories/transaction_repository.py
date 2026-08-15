from datetime import date
from decimal import Decimal

from sqlalchemy import Integer, case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.expense_category import ExpenseCategory
from app.models.property import Property
from app.models.renter import Renter
from app.models.supplier import Supplier
from app.models.transaction import Transaction, TransactionTypeEnum
from typing import Optional

# The date a transaction belongs to for the user: the month rent was paid *for*
# when there is one (revenue), otherwise the day the money moved (expenses).
EFFECTIVE_DATE = func.coalesce(Transaction.month_for, Transaction.date_of_payment)


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
        owner_id: str,
    ) -> Transaction | None:
        stmt = (
            select(Transaction)
            .where(
                Transaction.id == transaction_id,
                Transaction.owner_id == owner_id,
            )
            .options(
                selectinload(Transaction.property),
                selectinload(Transaction.renter),
                selectinload(Transaction.category),
                selectinload(Transaction.categories),
                selectinload(Transaction.supplier),
            )
        )
        return self.session.scalar(stmt)

    def list(
        self,
        owner_id: str,
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
            .outerjoin(Property, Transaction.property_id == Property.id)
            .outerjoin(Renter, Transaction.renter_id == Renter.id)
            .outerjoin(ExpenseCategory, Transaction.category_id == ExpenseCategory.id)
            .outerjoin(Supplier, Transaction.supplier_id == Supplier.id)
            .where(Transaction.owner_id == owner_id)
            .options(
                selectinload(Transaction.property),
                selectinload(Transaction.renter),
                selectinload(Transaction.category),
                selectinload(Transaction.categories),
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
                    Transaction.property_address.ilike(search),
                    Transaction.renter_name.ilike(search),
                    ExpenseCategory.key.ilike(search),
                    Supplier.name.ilike(search),
                    Transaction.notes.ilike(search),
                )
            )
        stmt = stmt.order_by(
            EFFECTIVE_DATE.desc(),
            # Load-bearing tiebreaker: a bulk revenue batch shares one month_for.
            Transaction.created_at.desc(),
        ).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def get_monthly_summary(self, owner_id: str, from_date: date) -> list:
        # Bucketed by the effective date, so the chart's bars line up with the month
        # sections of the list below it (and with the income/expense report).
        year_col = func.extract('year', EFFECTIVE_DATE).cast(Integer)
        month_col = func.extract('month', EFFECTIVE_DATE).cast(Integer)
        stmt = (
            select(
                year_col.label('year'),
                month_col.label('month'),
                func.sum(
                    case((Transaction.type == TransactionTypeEnum.REVENUE, Transaction.amount), else_=0)
                ).label('revenue'),
                func.sum(
                    case((Transaction.type == TransactionTypeEnum.EXPENSE, Transaction.amount), else_=0)
                ).label('expenses'),
            )
            .where(
                Transaction.owner_id == owner_id,
                EFFECTIVE_DATE >= from_date,
            )
            .group_by(year_col, month_col)
            .order_by(year_col, month_col)
        )
        return list(self.session.execute(stmt).all())

    def update(self, transaction_id: int, owner_id: str, fields: dict, new_categories: Optional[list] = None) -> Transaction | None:
        transaction = self.get_by_id(transaction_id, owner_id)
        if transaction is None:
            return None
        for key, value in fields.items():
            setattr(transaction, key, value)
        if new_categories is not None:
            transaction.categories = new_categories
        self.session.commit()
        self.session.refresh(transaction)
        return transaction

    def delete(self, transaction_id: int, owner_id: str) -> bool:
        transaction = self.get_by_id(transaction_id, owner_id)
        if transaction is None:
            return False
        self.session.delete(transaction)
        self.session.commit()
        return True

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.expense_category import ExpenseCategory


class ExpenseCategoryRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_all_active_ordered(self) -> list[ExpenseCategory]:
        stmt = (
            select(ExpenseCategory)
            .where(ExpenseCategory.is_active == True)
            .order_by(ExpenseCategory.sort_order)
        )
        return list(self.session.scalars(stmt).all())

    def get_by_id(self, category_id: int) -> ExpenseCategory | None:
        stmt = select(ExpenseCategory).where(ExpenseCategory.id == category_id)
        return self.session.scalar(stmt)

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.expense_category import ExpenseCategory


class ExpenseCategoryRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_all_active_ordered(self, owner_id: int) -> list[ExpenseCategory]:
        """Return predefined (owner_id IS NULL) and user-created (owner_id = owner_id) categories."""
        stmt = (
            select(ExpenseCategory)
            .where(
                (ExpenseCategory.owner_id.is_(None)) | (ExpenseCategory.owner_id == owner_id),
                ExpenseCategory.is_active == True,
            )
            .order_by(ExpenseCategory.sort_order)
        )
        return list(self.session.scalars(stmt).all())

    def get_by_id(self, category_id: int) -> ExpenseCategory | None:
        stmt = select(ExpenseCategory).where(ExpenseCategory.id == category_id)
        return self.session.scalar(stmt)

    def get_max_sort_order(self) -> int:
        result = self.session.scalar(select(func.coalesce(func.max(ExpenseCategory.sort_order), 0)))
        return result or 0

    def create(self, category: ExpenseCategory) -> ExpenseCategory:
        self.session.add(category)
        self.session.commit()
        self.session.refresh(category)
        return category

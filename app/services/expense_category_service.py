from fastapi import HTTPException

from app.models.expense_category import ExpenseCategory
from app.repositories.expense_category_repository import ExpenseCategoryRepository
from app.schemas.expense_category import ExpenseCategoryCreate, ExpenseCategoryRead


class ExpenseCategoryService:
    def __init__(self, expense_category_repository: ExpenseCategoryRepository):
        self.expense_category_repository = expense_category_repository

    def list_active(self, owner_id: str) -> list[ExpenseCategoryRead]:
        categories = self.expense_category_repository.get_all_active_ordered(owner_id)
        return [ExpenseCategoryRead.model_validate(c) for c in categories]

    def create(self, data: ExpenseCategoryCreate, owner_id: str) -> ExpenseCategoryRead:
        max_sort = self.expense_category_repository.get_max_sort_order()
        category = ExpenseCategory(
            key=None,
            name=data.name.strip(),
            owner_id=owner_id,
            is_active=True,
            sort_order=max_sort + 1,
        )
        created = self.expense_category_repository.create(category)
        return ExpenseCategoryRead.model_validate(created)

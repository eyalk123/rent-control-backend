from fastapi import HTTPException

from app.entity_colors import least_used_color
from app.models.expense_category import ExpenseCategory
from app.models.owner import Owner
from app.repositories.expense_category_repository import ExpenseCategoryRepository
from app.schemas.expense_category import ExpenseCategoryCreate, ExpenseCategoryRead
from app.services import country_service

# The built-in categories withdrawn where the mortgage feature is on: its monthly payment
# replaces "Mortgage interest", and offering both would subtract the mortgage twice.
# Withdrawn from what can be *picked* only — categories are never deleted, so an expense
# already filed under one keeps it, and reports and the export still show it.
_REPLACED_BY_MORTGAGE = frozenset({"mortgage_interest"})


def pickable_categories(
    repository: ExpenseCategoryRepository, owner_id: str
) -> list[ExpenseCategory]:
    """The active categories this owner may choose from: the picker and the receipt
    scanner's catalogue both read this, so the AI cannot suggest one the form would not offer."""
    categories = repository.get_all_active_ordered(owner_id)
    owner = repository.session.get(Owner, owner_id)
    if country_service.capabilities_for(owner.country if owner else None).mortgage:
        categories = [c for c in categories if c.key not in _REPLACED_BY_MORTGAGE]
    return categories


class ExpenseCategoryService:
    def __init__(self, expense_category_repository: ExpenseCategoryRepository):
        self.expense_category_repository = expense_category_repository

    def list_active(self, owner_id: str) -> list[ExpenseCategoryRead]:
        categories = pickable_categories(self.expense_category_repository, owner_id)
        return [ExpenseCategoryRead.model_validate(c) for c in categories]

    def create(self, data: ExpenseCategoryCreate, owner_id: str) -> ExpenseCategoryRead:
        max_sort = self.expense_category_repository.get_max_sort_order()
        category = ExpenseCategory(
            key=None,
            name=data.name.strip(),
            owner_id=owner_id,
            is_active=True,
            sort_order=max_sort + 1,
            color=least_used_color(self.expense_category_repository.colors_in_use(owner_id)),
        )
        created = self.expense_category_repository.create(category)
        return ExpenseCategoryRead.model_validate(created)

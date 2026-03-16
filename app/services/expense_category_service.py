from app.repositories.expense_category_repository import ExpenseCategoryRepository


class ExpenseCategoryService:
    def __init__(self, expense_category_repository: ExpenseCategoryRepository):
        self.expense_category_repository = expense_category_repository

    def list_active(self):
        return self.expense_category_repository.get_all_active_ordered()

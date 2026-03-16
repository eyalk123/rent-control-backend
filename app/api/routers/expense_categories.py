from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_expense_category_service
from app.schemas.expense_category import ExpenseCategoryRead
from app.services.expense_category_service import ExpenseCategoryService

router = APIRouter()


@router.get("", response_model=list[ExpenseCategoryRead])
def list_expense_categories(
    expense_category_service: Annotated[
        ExpenseCategoryService, Depends(get_expense_category_service)
    ],
):
    """List active expense categories ordered by sort_order."""
    return expense_category_service.list_active()

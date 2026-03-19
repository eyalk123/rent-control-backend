from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user, get_expense_category_service
from app.schemas.expense_category import ExpenseCategoryCreate, ExpenseCategoryRead
from app.services.expense_category_service import ExpenseCategoryService

router = APIRouter()


@router.get("", response_model=list[ExpenseCategoryRead])
def list_expense_categories(
    current_user: Annotated[dict, Depends(get_current_user)],
    expense_category_service: Annotated[
        ExpenseCategoryService, Depends(get_expense_category_service)
    ],
):
    """List active expense categories ordered by sort_order."""
    return expense_category_service.list_active(owner_id=current_user["user_id"])


@router.post("", response_model=ExpenseCategoryRead, status_code=201)
def create_expense_category(
    data: ExpenseCategoryCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    expense_category_service: Annotated[
        ExpenseCategoryService, Depends(get_expense_category_service)
    ],
):
    """Create a user-defined expense category."""
    return expense_category_service.create(data, owner_id=current_user["user_id"])

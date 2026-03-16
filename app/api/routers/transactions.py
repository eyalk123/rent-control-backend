from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_current_user, get_transaction_service
from app.schemas.transaction import (
    TransactionCreateExpense,
    TransactionCreateRevenue,
    TransactionRead,
)
from app.services.transaction_service import TransactionService

router = APIRouter()


@router.get("", response_model=list[TransactionRead])
def list_transactions(
    current_user: Annotated[dict, Depends(get_current_user)],
    transaction_service: Annotated[TransactionService, Depends(get_transaction_service)],
    type_filter: str | None = Query(None, alias="type"),
    property_id: int | None = Query(None),
    renter_id: int | None = Query(None),
    q: str | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List transactions for the current user with optional filters."""
    return transaction_service.list_transactions(
        owner_id=current_user["user_id"],
        type_filter=type_filter,
        property_id=property_id,
        renter_id=renter_id,
        q=q,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )


@router.get("/{transaction_id}", response_model=TransactionRead)
def get_transaction(
    transaction_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    transaction_service: Annotated[TransactionService, Depends(get_transaction_service)],
):
    """Get a single transaction by id."""
    transaction = transaction_service.get_transaction(
        transaction_id, owner_id=current_user["user_id"]
    )
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction


@router.post("/revenue", response_model=TransactionRead, status_code=201)
def create_revenue(
    data: TransactionCreateRevenue,
    current_user: Annotated[dict, Depends(get_current_user)],
    transaction_service: Annotated[TransactionService, Depends(get_transaction_service)],
):
    """Create a revenue transaction."""
    return transaction_service.create_revenue(data, owner_id=current_user["user_id"])


@router.post("/expense", response_model=TransactionRead, status_code=201)
def create_expense(
    data: TransactionCreateExpense,
    current_user: Annotated[dict, Depends(get_current_user)],
    transaction_service: Annotated[TransactionService, Depends(get_transaction_service)],
):
    """Create an expense transaction."""
    return transaction_service.create_expense(data, owner_id=current_user["user_id"])

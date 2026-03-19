from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_current_user, get_supplier_service
from app.schemas.supplier import SupplierCreate, SupplierRead, SupplierUpdate
from app.services.supplier_service import SupplierService

router = APIRouter()


@router.get("", response_model=list[SupplierRead])
def list_suppliers(
    current_user: Annotated[dict, Depends(get_current_user)],
    supplier_service: Annotated[SupplierService, Depends(get_supplier_service)],
    category_id: int | None = Query(None),
    q: str | None = Query(None),
    include_inactive: bool = Query(False),
):
    """List suppliers, optionally filtered by category and/or search."""
    return supplier_service.list_suppliers(
        owner_id=current_user["user_id"],
        category_id=category_id,
        q=q,
        include_inactive=include_inactive,
    )


@router.get("/{supplier_id}", response_model=SupplierRead)
def get_supplier(
    supplier_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    supplier_service: Annotated[SupplierService, Depends(get_supplier_service)],
):
    """Get a single supplier by id."""
    supplier = supplier_service.get_supplier(supplier_id, owner_id=current_user["user_id"])
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier


@router.post("", response_model=SupplierRead, status_code=201)
def create_supplier(
    data: SupplierCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    supplier_service: Annotated[SupplierService, Depends(get_supplier_service)],
):
    """Create a new supplier."""
    return supplier_service.create_supplier(data, owner_id=current_user["user_id"])


@router.patch("/{supplier_id}", response_model=SupplierRead)
def update_supplier(
    supplier_id: int,
    data: SupplierUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
    supplier_service: Annotated[SupplierService, Depends(get_supplier_service)],
):
    """Partially update a supplier."""
    supplier = supplier_service.update_supplier(
        supplier_id, data, owner_id=current_user["user_id"]
    )
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier

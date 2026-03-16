from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_supplier_service
from app.schemas.supplier import SupplierRead
from app.services.supplier_service import SupplierService

router = APIRouter()


@router.get("", response_model=list[SupplierRead])
def list_suppliers(
    supplier_service: Annotated[SupplierService, Depends(get_supplier_service)],
    category_id: int | None = Query(None),
    q: str | None = Query(None),
):
    """List suppliers, optionally filtered by category and/or name search."""
    return supplier_service.list_suppliers(category_id=category_id, q=q)

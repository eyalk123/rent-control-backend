from fastapi import HTTPException

from app.models.supplier import Supplier
from app.repositories.expense_category_repository import ExpenseCategoryRepository
from app.repositories.supplier_repository import SupplierRepository
from app.schemas.supplier import SupplierCreate, SupplierRead, SupplierUpdate


class SupplierService:
    def __init__(
        self,
        supplier_repository: SupplierRepository,
        expense_category_repository: ExpenseCategoryRepository,
    ):
        self.supplier_repository = supplier_repository
        self.expense_category_repository = expense_category_repository

    def list_suppliers(
        self,
        owner_id: int,
        category_id: int | None = None,
        q: str | None = None,
        include_inactive: bool = False,
    ) -> list[SupplierRead]:
        suppliers = self.supplier_repository.get_all(
            owner_id=owner_id,
            category_id=category_id,
            q=q,
            include_inactive=include_inactive,
        )
        return [SupplierRead.model_validate(s) for s in suppliers]

    def get_supplier(self, supplier_id: int, owner_id: int) -> SupplierRead | None:
        supplier = self.supplier_repository.get_by_id(supplier_id, owner_id)
        if supplier is None:
            return None
        return SupplierRead.model_validate(supplier)

    def _validate_category_ids(self, category_ids: list[int], owner_id: int) -> None:
        """Validate that all category_ids exist and are accessible to the owner."""
        for cat_id in category_ids:
            cat = self.expense_category_repository.get_by_id(cat_id)
            if cat is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Expense category {cat_id} not found",
                )
            if cat.owner_id is not None and cat.owner_id != owner_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"Expense category {cat_id} not found",
                )

    def create_supplier(self, data: SupplierCreate, owner_id: int) -> SupplierRead:
        self._validate_category_ids(data.category_ids, owner_id)
        supplier = Supplier(
            owner_id=owner_id,
            name=data.name.strip(),
            is_active=True,
            phone=data.phone,
            email=data.email,
            notes=data.notes,
        )
        created = self.supplier_repository.create(supplier, data.category_ids)
        return SupplierRead.model_validate(created)

    def update_supplier(
        self,
        supplier_id: int,
        data: SupplierUpdate,
        owner_id: int,
    ) -> SupplierRead | None:
        if data.category_ids is not None:
            self._validate_category_ids(data.category_ids, owner_id)
        name = data.name.strip() if data.name and data.name.strip() else None
        updated = self.supplier_repository.update(
            supplier_id,
            owner_id,
            name=name,
            phone=data.phone,
            email=data.email,
            notes=data.notes,
            category_ids=data.category_ids,
            is_active=data.is_active,
        )
        if updated is None:
            return None
        return SupplierRead.model_validate(updated)

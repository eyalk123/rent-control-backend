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
        owner_id: str,
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

    def get_supplier(self, supplier_id: int, owner_id: str) -> SupplierRead | None:
        supplier = self.supplier_repository.get_by_id(supplier_id, owner_id)
        if supplier is None:
            return None
        return SupplierRead.model_validate(supplier)

    def _validate_category_ids(self, category_ids: list[int], owner_id: str) -> None:
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

    def create_supplier(self, data: SupplierCreate, owner_id: str) -> SupplierRead:
        self._validate_category_ids(data.category_ids, owner_id)
        supplier = Supplier(
            owner_id=owner_id,
            name=data.name.strip(),
            is_active=True,
            phone=data.phone,
            email=data.email,
            notes=data.notes,
            bank_account=data.bank_account,
        )
        created = self.supplier_repository.create(supplier, data.category_ids)
        return SupplierRead.model_validate(created)

    def update_supplier(
        self,
        supplier_id: int,
        data: SupplierUpdate,
        owner_id: str,
    ) -> SupplierRead | None:
        # Only the fields explicitly present in the request are applied, so a
        # client can clear an optional field by sending it as null. Omitted
        # fields are left untouched.
        fields = data.model_dump(exclude_unset=True)

        category_ids = fields.pop("category_ids", None)
        if category_ids is not None:
            self._validate_category_ids(category_ids, owner_id)

        # name is non-nullable: ignore an empty/whitespace value instead of
        # clearing it.
        if "name" in fields:
            name = fields["name"]
            if name is None or not str(name).strip():
                fields.pop("name")
            else:
                fields["name"] = str(name).strip()

        updated = self.supplier_repository.update(
            supplier_id,
            owner_id,
            fields=fields,
            category_ids=category_ids,
        )
        if updated is None:
            return None
        return SupplierRead.model_validate(updated)

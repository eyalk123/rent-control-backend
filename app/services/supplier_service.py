from app.repositories.supplier_repository import SupplierRepository


class SupplierService:
    def __init__(self, supplier_repository: SupplierRepository):
        self.supplier_repository = supplier_repository

    def list_suppliers(self, category_id: int | None = None, q: str | None = None):
        return self.supplier_repository.get_all(category_id=category_id, q=q)

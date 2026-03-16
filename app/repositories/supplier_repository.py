from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.supplier import Supplier


class SupplierRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_all(
        self,
        category_id: int | None = None,
        q: str | None = None,
    ) -> list[Supplier]:
        stmt = select(Supplier).where(Supplier.is_active == True)
        if category_id is not None:
            stmt = stmt.where(Supplier.category_id == category_id)
        if q and q.strip():
            search = f"%{q.strip()}%"
            stmt = stmt.where(Supplier.name.ilike(search))
        stmt = stmt.order_by(Supplier.name)
        return list(self.session.scalars(stmt).all())

    def get_by_id(self, supplier_id: int) -> Supplier | None:
        stmt = select(Supplier).where(Supplier.id == supplier_id)
        return self.session.scalar(stmt)

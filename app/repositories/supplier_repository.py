from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.expense_category import ExpenseCategory
from app.models.supplier import Supplier, supplier_categories


class SupplierRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_all(
        self,
        owner_id: int,
        category_id: int | None = None,
        q: str | None = None,
        include_inactive: bool = False,
    ) -> list[Supplier]:
        stmt = (
            select(Supplier)
            .where(Supplier.owner_id == owner_id)
            .options(selectinload(Supplier.categories))
        )
        if not include_inactive:
            stmt = stmt.where(Supplier.is_active == True)
        if category_id is not None:
            stmt = stmt.join(Supplier.categories).where(ExpenseCategory.id == category_id)
        if q and q.strip():
            search = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    Supplier.name.ilike(search),
                    Supplier.phone.ilike(search),
                    Supplier.email.ilike(search),
                )
            )
        stmt = stmt.order_by(Supplier.name)
        return list(self.session.scalars(stmt).unique().all())

    def get_by_id(self, supplier_id: int, owner_id: int | None = None) -> Supplier | None:
        stmt = (
            select(Supplier)
            .where(Supplier.id == supplier_id)
            .options(selectinload(Supplier.categories))
        )
        if owner_id is not None:
            stmt = stmt.where(Supplier.owner_id == owner_id)
        return self.session.scalar(stmt)

    def create(self, supplier: Supplier, category_ids: list[int]) -> Supplier:
        self.session.add(supplier)
        self.session.flush()
        for cat_id in category_ids:
            self.session.execute(
                supplier_categories.insert().values(
                    supplier_id=supplier.id,
                    category_id=cat_id,
                )
            )
        self.session.commit()
        return self.get_by_id(supplier.id, supplier.owner_id) or supplier

    def update(
        self,
        supplier_id: int,
        owner_id: int,
        *,
        name: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        notes: str | None = None,
        category_ids: list[int] | None = None,
        is_active: bool | None = None,
    ) -> Supplier | None:
        supplier = self.get_by_id(supplier_id, owner_id)
        if supplier is None:
            return None
        if name is not None:
            supplier.name = name
        if phone is not None:
            supplier.phone = phone
        if email is not None:
            supplier.email = email
        if notes is not None:
            supplier.notes = notes
        if is_active is not None:
            supplier.is_active = is_active
        if category_ids is not None:
            self.session.execute(
                supplier_categories.delete().where(supplier_categories.c.supplier_id == supplier_id)
            )
            for cat_id in category_ids:
                self.session.execute(
                    supplier_categories.insert().values(
                        supplier_id=supplier_id,
                        category_id=cat_id,
                    )
                )
        self.session.commit()
        return self.get_by_id(supplier_id, owner_id) or supplier

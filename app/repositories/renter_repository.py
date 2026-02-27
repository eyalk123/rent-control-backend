from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.property import Property
from app.models.renter import Renter


class RenterRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_all(self, owner_id: int | None = None) -> list[Renter]:
        stmt = select(Renter).options(selectinload(Renter.property))
        if owner_id is not None:
            stmt = stmt.join(Property).where(Property.owner_id == owner_id)
        return list(self.session.scalars(stmt).all())

    def get_by_id(self, renter_id: int) -> Renter | None:
        stmt = (
            select(Renter)
            .where(Renter.id == renter_id)
            .options(selectinload(Renter.property))
        )
        return self.session.scalar(stmt)

    def create(self, renter: Renter) -> Renter:
        self.session.add(renter)
        self.session.commit()
        self.session.refresh(renter)
        return renter

    def update(self, renter: Renter, data: dict) -> Renter:
        nullable_fields = {"property_id"}
        for key, value in data.items():
            if hasattr(renter, key) and (value is not None or key in nullable_fields):
                setattr(renter, key, value)
        self.session.commit()
        self.session.refresh(renter)
        return renter

    def delete(self, renter_id: int) -> bool:
        renter = self.get_by_id(renter_id)
        if renter is None:
            return False
        self.session.delete(renter)
        self.session.commit()
        return True

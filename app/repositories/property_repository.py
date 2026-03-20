from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.models.property import Property
from app.models.renter import Renter


class PropertyRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_all_by_owner(self, owner_id: int) -> list[Property]:
        stmt = (
            select(Property)
            .where(Property.owner_id == owner_id)
            .options(selectinload(Property.renters))
        )
        return list(self.session.scalars(stmt).all())

    def get_by_id(self, property_id: int, owner_id: int) -> Property | None:
        stmt = (
            select(Property)
            .where(Property.id == property_id, Property.owner_id == owner_id)
            .options(selectinload(Property.renters))
        )
        return self.session.scalar(stmt)

    def create(self, property: Property) -> Property:
        self.session.add(property)
        self.session.commit()
        self.session.refresh(property)
        return property

    def update(self, property: Property, data: dict) -> Property:
        nullable_fields = {
            "image_url",
            "property_owner",
            "number_of_rooms",
            "parking_numbers",
            "electricity_meter_number",
            "water_meter_tax",
            "property_tax",
            "house_committee",
        }
        for key, value in data.items():
            if hasattr(property, key) and (value is not None or key in nullable_fields):
                setattr(property, key, value)
        self.session.commit()
        self.session.refresh(property)
        return property

    def delete(self, property_id: int, owner_id: int) -> bool:
        property = self.get_by_id(property_id, owner_id)
        if property is None:
            return False
        # Unassign renters first
        stmt = update(Renter).where(Renter.property_id == property_id).values(property_id=None)
        self.session.execute(stmt)
        # Delete property
        self.session.delete(property)
        self.session.commit()
        return True

    def update_image_url(self, property_id: int, owner_id: int, image_url: str) -> Property | None:
        stmt = (
            select(Property)
            .where(Property.id == property_id, Property.owner_id == owner_id)
        )
        property = self.session.scalar(stmt)
        if property is None:
            return None
        property.image_url = image_url
        self.session.commit()
        self.session.refresh(property)
        return property

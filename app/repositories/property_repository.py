from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.models.property import Property
from app.models.renter import Renter


class PropertyRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_all_by_owner(self, owner_id: str) -> list[Property]:
        stmt = (
            select(Property)
            .where(Property.owner_id == owner_id)
            .options(selectinload(Property.renters))
        )
        return list(self.session.scalars(stmt).all())

    def has_any(self, owner_id: str) -> bool:
        """Whether this owner has a single property. Cheap — no rows are loaded.

        Used to decide whether the account's currency may still change: every property
        freezes its currency at creation, so once one exists, changing the account's would
        relabel amounts already recorded without re-denominating them.
        """
        stmt = select(Property.id).where(Property.owner_id == owner_id).limit(1)
        return self.session.scalar(stmt) is not None

    def get_by_id(self, property_id: int, owner_id: str) -> Property | None:
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
            "electricity_account_number",
            "water_meter_number",
            "water_account_number",
            "property_tax",
            "house_committee",
            "basic_contract_url",
            "land_registry_url",
            "floor",
            "apartment",
            "block",
            "plot",
        }
        for key, value in data.items():
            if hasattr(property, key) and (value is not None or key in nullable_fields):
                setattr(property, key, value)
        self.session.commit()
        self.session.refresh(property)
        return property

    def delete(self, property_id: int, owner_id: str) -> bool:
        property = self.get_by_id(property_id, owner_id)
        if property is None:
            return False
        self.delete_obj(property)
        return True

    def delete_obj(self, property: Property) -> None:
        stmt = update(Renter).where(Renter.property_id == property.id).values(property_id=None)
        self.session.execute(stmt)
        self.session.delete(property)
        self.session.commit()

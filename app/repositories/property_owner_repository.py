import json

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.notification_rule import NotificationRule
from app.models.property import Property
from app.models.property_owner import PropertyOwner


class PropertyOwnerRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_all(
        self, owner_id: str, q: str | None = None, include_inactive: bool = False
    ) -> list[PropertyOwner]:
        stmt = select(PropertyOwner).where(PropertyOwner.owner_id == owner_id)
        if not include_inactive:
            stmt = stmt.where(PropertyOwner.is_active == True)  # noqa: E712
        if q and q.strip():
            search = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    PropertyOwner.name.ilike(search),
                    PropertyOwner.phone.ilike(search),
                    PropertyOwner.email.ilike(search),
                )
            )
        return list(self.session.scalars(stmt.order_by(PropertyOwner.name)).all())

    def get_by_id(self, property_owner_id: int, owner_id: str) -> PropertyOwner | None:
        stmt = select(PropertyOwner).where(
            PropertyOwner.id == property_owner_id, PropertyOwner.owner_id == owner_id
        )
        return self.session.scalar(stmt)

    def get_by_name(self, name: str, owner_id: str) -> PropertyOwner | None:
        """Exact match, as typed — names are unique per account."""
        stmt = select(PropertyOwner).where(
            PropertyOwner.name == name, PropertyOwner.owner_id == owner_id
        )
        return self.session.scalar(stmt)

    def property_counts(self, owner_id: str) -> dict[int, int]:
        stmt = (
            select(Property.property_owner_id, func.count(Property.id))
            .where(Property.owner_id == owner_id, Property.property_owner_id.is_not(None))
            .group_by(Property.property_owner_id)
        )
        return {row[0]: row[1] for row in self.session.execute(stmt).all()}

    def create(self, property_owner: PropertyOwner, *, commit: bool = True) -> PropertyOwner:
        self.session.add(property_owner)
        if commit:
            self.session.commit()
            self.session.refresh(property_owner)
        else:
            self.session.flush()
        return property_owner

    _UPDATABLE_FIELDS = ("name", "phone", "email", "notes", "bank_account", "is_active")

    def update(self, property_owner: PropertyOwner, fields: dict) -> PropertyOwner:
        for key in self._UPDATABLE_FIELDS:
            if key in fields:
                setattr(property_owner, key, fields[key])
        self.session.commit()
        self.session.refresh(property_owner)
        return property_owner

    def rename_in_notification_scopes(self, owner_id: str, old: str, new: str) -> None:
        """Notification rules scope by owner *name*; move them with a rename, uncommitted.

        Left alone, a rule scoped to "Dad" would silently stop matching once Dad became
        "Dad (Haifa)" — and an empty match is not an error anyone would see.
        """
        rules = self.session.scalars(
            select(NotificationRule).where(NotificationRule.owner_id == owner_id)
        ).all()
        for rule in rules:
            try:
                names = json.loads(rule.scope_property_owners or "[]")
            except (ValueError, TypeError):
                continue
            if old in names:
                rule.scope_property_owners = json.dumps(
                    list(dict.fromkeys(new if n == old else n for n in names))
                )

    def delete(self, property_owner: PropertyOwner) -> None:
        self.session.delete(property_owner)
        self.session.commit()

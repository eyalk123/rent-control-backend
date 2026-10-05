from fastapi import HTTPException

from app.models.property_owner import PropertyOwner
from app.repositories.property_owner_repository import PropertyOwnerRepository
from app.schemas.property_owner import (
    PropertyOwnerCreate,
    PropertyOwnerRead,
    PropertyOwnerUpdate,
)


class PropertyOwnerService:
    """The human owners of the account's properties — a contact list, like suppliers.

    The name is the one rule with teeth: unique per account and never empty, because the
    rest of the product (reports, notification scopes, filters) still refers to an owner
    by it.
    """

    def __init__(self, repository: PropertyOwnerRepository):
        self.repository = repository

    def _read(self, owner: PropertyOwner, counts: dict[int, int]) -> PropertyOwnerRead:
        read = PropertyOwnerRead.model_validate(owner)
        read.property_count = counts.get(owner.id, 0)
        return read

    def list_owners(
        self, owner_id: str, q: str | None = None, include_inactive: bool = False
    ) -> list[PropertyOwnerRead]:
        counts = self.repository.property_counts(owner_id)
        return [
            self._read(o, counts)
            for o in self.repository.get_all(owner_id, q=q, include_inactive=include_inactive)
        ]

    def get_owner(self, property_owner_id: int, owner_id: str) -> PropertyOwnerRead | None:
        owner = self.repository.get_by_id(property_owner_id, owner_id)
        if owner is None:
            return None
        return self._read(owner, self.repository.property_counts(owner_id))

    def _require_free_name(self, name: str, owner_id: str, current_id: int | None = None) -> None:
        existing = self.repository.get_by_name(name, owner_id)
        if existing is not None and existing.id != current_id:
            raise HTTPException(
                status_code=409, detail="A property owner with this name already exists"
            )

    def create_owner(self, data: PropertyOwnerCreate, owner_id: str) -> PropertyOwnerRead:
        self._require_free_name(data.name, owner_id)
        created = self.repository.create(
            PropertyOwner(
                owner_id=owner_id,
                name=data.name,
                is_active=True,
                phone=data.phone,
                email=data.email,
                notes=data.notes,
                bank_account=data.bank_account,
            )
        )
        return self._read(created, {})

    def update_owner(
        self, property_owner_id: int, data: PropertyOwnerUpdate, owner_id: str
    ) -> PropertyOwnerRead | None:
        owner = self.repository.get_by_id(property_owner_id, owner_id)
        if owner is None:
            return None
        # Only the fields present in the request are applied, so an optional field can be
        # cleared by sending null. The name cannot: an empty one is ignored, as on suppliers.
        fields = data.model_dump(exclude_unset=True)
        if "name" in fields and not fields["name"]:
            fields.pop("name")
        if "is_active" in fields and fields["is_active"] is None:
            fields.pop("is_active")
        if "name" in fields and fields["name"] != owner.name:
            self._require_free_name(fields["name"], owner_id, current_id=owner.id)
            self.repository.rename_in_notification_scopes(owner_id, owner.name, fields["name"])
        updated = self.repository.update(owner, fields)
        return self._read(updated, self.repository.property_counts(owner_id))

    def delete_owner(self, property_owner_id: int, owner_id: str) -> bool:
        owner = self.repository.get_by_id(property_owner_id, owner_id)
        if owner is None:
            return False
        if self.repository.property_counts(owner_id).get(owner.id):
            raise HTTPException(
                status_code=409,
                detail="This owner still has properties. Move them to another owner first.",
            )
        self.repository.delete(owner)
        return True

    def resolve_name(self, name: str, owner_id: str) -> PropertyOwner:
        """The owner with this exact name, created if there is none — uncommitted.

        Backs the legacy `property_owner` text field on the property form, which clients
        already in the stores still send. It is what that field always did: typing a new
        name made a new owner.
        """
        existing = self.repository.get_by_name(name, owner_id)
        if existing is not None:
            return existing
        return self.repository.create(
            PropertyOwner(owner_id=owner_id, name=name, is_active=True), commit=False
        )

    def require_owned(self, property_owner_id: int, owner_id: str) -> PropertyOwner:
        owner = self.repository.get_by_id(property_owner_id, owner_id)
        if owner is None:
            raise HTTPException(status_code=400, detail=f"Property owner {property_owner_id} not found")
        return owner

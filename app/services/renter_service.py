from fastapi import HTTPException

from app.models.renter import Renter
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.schemas.renter import RenterCreate, RenterUpdate


class RenterService:
    def __init__(
        self,
        renter_repository: RenterRepository,
        property_repository: PropertyRepository,
    ):
        self.renter_repository = renter_repository
        self.property_repository = property_repository

    def list_renters(self, owner_id: int):
        return self.renter_repository.get_all(owner_id=owner_id)

    def get_renter(self, renter_id: int, owner_id: int):
        renter = self.renter_repository.get_by_id(renter_id)
        if renter is None:
            return None
        self._check_renter_access(renter, owner_id)
        return renter

    def _check_renter_access(self, renter: Renter, owner_id: int) -> None:
        if renter.property_id is not None and renter.property is not None:
            if renter.property.owner_id != owner_id:
                raise HTTPException(status_code=403, detail="Access denied")

    def create_renter(self, data: RenterCreate, owner_id: int):
        if data.property_id is not None:
            property = self.property_repository.get_by_id(data.property_id, owner_id)
            if property is None:
                raise HTTPException(status_code=403, detail="Property not found or access denied")
        renter = Renter(
            property_id=data.property_id,
            first_name=data.first_name,
            last_name=data.last_name,
            phone=data.phone,
            email=data.email,
            monthly_rent=data.monthly_rent,
            lease_start=data.lease_start,
            lease_end=data.lease_end,
        )
        return self.renter_repository.create(renter)

    def update_renter(self, renter_id: int, data: RenterUpdate, owner_id: int):
        renter = self.renter_repository.get_by_id(renter_id)
        if renter is None:
            return None
        self._check_renter_access(renter, owner_id)
        update_dict = data.model_dump(exclude_unset=True)
        if "property_id" in update_dict and update_dict["property_id"] is not None:
            property = self.property_repository.get_by_id(
                update_dict["property_id"], owner_id
            )
            if property is None:
                raise HTTPException(status_code=403, detail="Property not found or access denied")
        return self.renter_repository.update(renter, update_dict)

    def delete_renter(self, renter_id: int, owner_id: int) -> bool:
        renter = self.renter_repository.get_by_id(renter_id)
        if renter is None:
            return False
        self._check_renter_access(renter, owner_id)
        return self.renter_repository.delete(renter_id)

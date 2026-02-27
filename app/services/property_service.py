from fastapi import UploadFile

from app.models.property import Property, PropertyTypeEnum
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.schemas.property import PropertyCreate, PropertyUpdate


class PropertyService:
    def __init__(
        self,
        property_repository: PropertyRepository,
        renter_repository: RenterRepository,
    ):
        self.property_repository = property_repository
        self.renter_repository = renter_repository

    def list_properties(self, owner_id: int):
        return self.property_repository.get_all_by_owner(owner_id)

    def get_property(self, property_id: int, owner_id: int):
        return self.property_repository.get_by_id(property_id, owner_id)

    def create_property(self, data: PropertyCreate, owner_id: int):
        property_type = PropertyTypeEnum(data.type.value)
        property = Property(
            owner_id=owner_id,
            address=data.address,
            city=data.city,
            zip_code=data.zip_code,
            type=property_type,
            sq_ft=data.sq_ft,
            purchase_price=data.purchase_price,
            image_url=data.image_url,
        )
        return self.property_repository.create(property)

    def update_property(self, property_id: int, data: PropertyUpdate, owner_id: int):
        property = self.property_repository.get_by_id(property_id, owner_id)
        if property is None:
            return None
        update_dict = data.model_dump(exclude_unset=True)
        if "type" in update_dict and update_dict["type"] is not None:
            update_dict["type"] = PropertyTypeEnum(update_dict["type"].value)
        return self.property_repository.update(property, update_dict)

    def delete_property(self, property_id: int, owner_id: int) -> bool:
        return self.property_repository.delete(property_id, owner_id)

    def upload_property_image(self, property_id: int, file: UploadFile, owner_id: int):
        property = self.property_repository.get_by_id(property_id, owner_id)
        if property is None:
            return None
        # Mock S3 upload - return a mock URL
        mock_url = f"https://mock-bucket.s3.amazonaws.com/properties/{property_id}/{file.filename}"
        return self.property_repository.update_image_url(property_id, owner_id, mock_url)

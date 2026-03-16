import json

from fastapi import UploadFile

from app.models.property import Property, PropertyTypeEnum
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.schemas.property import PropertyCreate, PropertyUpdate
from app.schemas.renter import PropertyRenterSummary


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
        parking_numbers_str = (
            json.dumps(data.parking_numbers) if data.parking_numbers is not None else None
        )
        property = Property(
            owner_id=owner_id,
            address=data.address,
            city=data.city,
            zip_code=data.zip_code,
            type=property_type,
            sq_ft=data.sq_ft,
            purchase_price=data.purchase_price,
            image_url=data.image_url,
            number_of_rooms=data.number_of_rooms,
            parking_numbers=parking_numbers_str,
            electricity_meter_number=data.electricity_meter_number,
            water_meter_tax=data.water_meter_tax,
            property_tax=data.property_tax,
            house_committee=data.house_committee,
        )
        created = self.property_repository.create(property)
        return self.property_repository.get_by_id(created.id, owner_id)

    def update_property(self, property_id: int, data: PropertyUpdate, owner_id: int):
        property = self.property_repository.get_by_id(property_id, owner_id)
        if property is None:
            return None
        update_dict = data.model_dump(exclude_unset=True)
        if "type" in update_dict and update_dict["type"] is not None:
            update_dict["type"] = PropertyTypeEnum(update_dict["type"].value)
        if "parking_numbers" in update_dict:
            update_dict["parking_numbers"] = (
                json.dumps(update_dict["parking_numbers"])
                if update_dict["parking_numbers"] is not None
                else None
            )
        self.property_repository.update(property, update_dict)
        return self.property_repository.get_by_id(property_id, owner_id)

    def delete_property(self, property_id: int, owner_id: int) -> bool:
        return self.property_repository.delete(property_id, owner_id)

    def upload_property_image(self, property_id: int, file: UploadFile, owner_id: int):
        property = self.property_repository.get_by_id(property_id, owner_id)
        if property is None:
            return None
        mock_url = f"https://mock-bucket.s3.amazonaws.com/properties/{property_id}/{file.filename}"
        self.property_repository.update_image_url(property_id, owner_id, mock_url)
        return self.property_repository.get_by_id(property_id, owner_id)

    def get_property_renters(self, property_id: int, owner_id: int):
        """Return renters linked to the property (active leases only) for e.g. add-revenue form."""
        property = self.property_repository.get_by_id(property_id, owner_id)
        if property is None:
            return None
        renters = self.renter_repository.get_by_property_id(
            property_id=property_id,
            owner_id=owner_id,
            active_only=True,
        )
        summaries = []
        for r in renters:
            lease_years_data = r.lease_years
            if isinstance(lease_years_data, str):
                lease_years_data = json.loads(lease_years_data)
            monthly_rent = 0.0
            if lease_years_data:
                monthly_rent = lease_years_data[0]["amount"] / 12
            summaries.append(
                PropertyRenterSummary(
                    id=r.id,
                    first_name=r.first_name,
                    last_name=r.last_name,
                    monthly_rent=monthly_rent,
                )
            )
        return summaries

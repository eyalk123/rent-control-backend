import enum

from sqlalchemy import Column, DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.orm import relationship

from app.clock import utc_now_naive
from app.models.base import Base


class PropertyTypeEnum(str, enum.Enum):
    """Every type the column can hold, everywhere.

    Nothing is removed per country — a PostgreSQL enum value cannot be dropped without
    rewriting the type and every column using it, and existing Israeli rows hold the last
    two. The skimmed set is produced by filtering what the clients offer and what the API
    accepts (``capabilities.israeli_property_types``), not by narrowing the storage.
    """

    APARTMENT = "apartment"
    HOUSE = "house"
    COMMERCIAL = "commercial"
    GARDEN_APARTMENT = "garden_apartment"  # Israel only
    HOUSING_UNIT = "housing_unit"  # Israel only
    CONDO_TOWNHOUSE = "condo_townhouse"
    ROOM = "room"
    OTHER = "other"


class Property(Base):
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False)
    address = Column(String, nullable=False)
    city = Column(String, nullable=False)
    # Optional everywhere: postal codes do not exist in parts of Ireland, the Gulf,
    # Africa and the Caribbean, and there is nothing to type.
    zip_code = Column(String, nullable=True)
    type = Column(
        Enum(PropertyTypeEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    # Square *metres*, despite the name. Converted for display where the country uses
    # square feet; the column name predates that and is not worth a migration.
    sq_ft = Column(Integer, nullable=True)
    # Optional: an inherited or managed property may have no price the owner knows or
    # wants to record, and demanding one at signup loses the account.
    purchase_price = Column(Float, nullable=True)
    image_url = Column(String, nullable=True)
    property_owner = Column(String, nullable=True)
    number_of_rooms = Column(Float, nullable=True)
    parking_numbers = Column(Text, nullable=True)  # JSON array of strings
    electricity_meter_number = Column(String, nullable=True)
    electricity_account_number = Column(String, nullable=True)
    water_meter_number = Column(String, nullable=True)
    water_account_number = Column(String, nullable=True)
    property_tax = Column(Float, nullable=True)
    house_committee = Column(Float, nullable=True)
    inventory_notes = Column(Text, nullable=True)
    basic_contract_url = Column(String, nullable=True)
    land_registry_url = Column(String, nullable=True)
    currency_code = Column(String, nullable=True)
    # ISO 3166-1 alpha-2, copied from the owner at creation and never shown as a picker.
    # **This is the value every country rule reads** — rules attach to where the building
    # is, not to where the account holder signed up. NULL resolves to Israel, since every
    # row predating this column was Israeli.
    country = Column(String(2), nullable=True)
    floor = Column(Integer, nullable=True)
    apartment = Column(String, nullable=True)
    block = Column(String, nullable=True)
    plot = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)

    renters = relationship("Renter", back_populates="property", foreign_keys="Renter.property_id")
    transactions = relationship("Transaction", back_populates="property", foreign_keys="Transaction.property_id", passive_deletes=True)
    files = relationship("PropertyFile", back_populates="property", cascade="all, delete-orphan")

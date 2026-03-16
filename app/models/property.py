import enum
from sqlalchemy import Column, Enum, Float, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import Base


class PropertyTypeEnum(str, enum.Enum):
    APARTMENT = "apartment"
    HOUSE = "house"
    COMMERCIAL = "commercial"


class Property(Base):
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, nullable=False)
    address = Column(String, nullable=False)
    city = Column(String, nullable=False)
    zip_code = Column(String, nullable=False)
    type = Column(
        Enum(PropertyTypeEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    sq_ft = Column(Integer, nullable=False)
    purchase_price = Column(Float, nullable=False)
    image_url = Column(String, nullable=True)
    number_of_rooms = Column(Integer, nullable=True)
    parking_numbers = Column(Text, nullable=True)  # JSON array of strings
    electricity_meter_number = Column(String, nullable=True)
    water_meter_tax = Column(Float, nullable=True)
    property_tax = Column(Float, nullable=True)
    house_committee = Column(Float, nullable=True)
    currency_code = Column(String, nullable=True)

    renters = relationship("Renter", back_populates="property", foreign_keys="Renter.property_id")
    transactions = relationship("Transaction", back_populates="property", foreign_keys="Transaction.property_id")

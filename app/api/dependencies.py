from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.services.property_service import PropertyService
from app.services.renter_service import RenterService


def get_current_user() -> dict:
    """Mock authentication - returns a simulated authenticated user."""
    return {"user_id": 1, "role": "owner"}


def get_property_repository(db: Annotated[Session, Depends(get_db)]) -> PropertyRepository:
    return PropertyRepository(db)


def get_renter_repository(db: Annotated[Session, Depends(get_db)]) -> RenterRepository:
    return RenterRepository(db)


def get_property_service(
    property_repository: Annotated[PropertyRepository, Depends(get_property_repository)],
    renter_repository: Annotated[RenterRepository, Depends(get_renter_repository)],
) -> PropertyService:
    return PropertyService(property_repository, renter_repository)


def get_renter_service(
    renter_repository: Annotated[RenterRepository, Depends(get_renter_repository)],
    property_repository: Annotated[PropertyRepository, Depends(get_property_repository)],
) -> RenterService:
    return RenterService(renter_repository, property_repository)

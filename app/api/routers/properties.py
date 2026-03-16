from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.dependencies import get_current_user, get_property_service
from app.schemas.property import PropertyCreate, PropertyRead, PropertyUpdate
from app.schemas.renter import PropertyRenterSummary
from app.services.property_service import PropertyService

router = APIRouter()


@router.get("", response_model=list[PropertyRead])
def list_properties(
    current_user: Annotated[dict, Depends(get_current_user)],
    property_service: Annotated[PropertyService, Depends(get_property_service)],
):
    """Returns a list of all properties for the current user."""
    properties = property_service.list_properties(owner_id=current_user["user_id"])
    return properties


@router.get("/{property_id}/renters", response_model=list[PropertyRenterSummary])
def list_property_renters(
    property_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    property_service: Annotated[PropertyService, Depends(get_property_service)],
):
    """Returns renters linked to the property (active leases) for e.g. add-revenue form."""
    renters = property_service.get_property_renters(property_id, owner_id=current_user["user_id"])
    if renters is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return renters


@router.get("/{property_id}", response_model=PropertyRead)
def get_property(
    property_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    property_service: Annotated[PropertyService, Depends(get_property_service)],
):
    """Returns property details with nested list of renters."""
    property = property_service.get_property(property_id, owner_id=current_user["user_id"])
    if property is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return property


@router.post("", response_model=PropertyRead, status_code=201)
def create_property(
    data: PropertyCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    property_service: Annotated[PropertyService, Depends(get_property_service)],
):
    """Creates a new property."""
    property = property_service.create_property(data, owner_id=current_user["user_id"])
    return property


@router.patch("/{property_id}", response_model=PropertyRead)
def update_property(
    property_id: int,
    data: PropertyUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
    property_service: Annotated[PropertyService, Depends(get_property_service)],
):
    """Partially updates a property."""
    property = property_service.update_property(property_id, data, owner_id=current_user["user_id"])
    if property is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return property


@router.delete("/{property_id}", status_code=204)
def delete_property(
    property_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    property_service: Annotated[PropertyService, Depends(get_property_service)],
):
    """Deletes a property. Assigned renters are unassigned (property_id set to null)."""
    deleted = property_service.delete_property(property_id, owner_id=current_user["user_id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Property not found")
    return None


@router.post("/{property_id}/image", response_model=PropertyRead)
def upload_image(
    property_id: int,
    file: Annotated[UploadFile, File()],
    current_user: Annotated[dict, Depends(get_current_user)],
    property_service: Annotated[PropertyService, Depends(get_property_service)],
):
    """Uploads an image for the property (multipart/form-data). Mocks S3 upload."""
    property = property_service.upload_property_image(property_id, file, owner_id=current_user["user_id"])
    if property is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return property

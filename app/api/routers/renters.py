from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_current_user, get_renter_service
from app.schemas.renter import RenterCreate, RenterListRead, RenterRead, RenterUpdate
from app.services.renter_service import RenterService

router = APIRouter()


@router.get("", response_model=list[RenterListRead])
def list_renters(
    current_user: Annotated[dict, Depends(get_current_user)],
    renter_service: Annotated[RenterService, Depends(get_renter_service)],
):
    """Returns a list of all renters with associated property details."""
    renters = renter_service.list_renters(owner_id=current_user["user_id"])
    return renters


@router.get("/{renter_id}", response_model=RenterRead)
def get_renter(
    renter_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    renter_service: Annotated[RenterService, Depends(get_renter_service)],
):
    """Returns full details of a specific renter."""
    renter = renter_service.get_renter(renter_id, owner_id=current_user["user_id"])
    if renter is None:
        raise HTTPException(status_code=404, detail="Renter not found")
    return renter


@router.post("", response_model=RenterRead, status_code=201)
def create_renter(
    data: RenterCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    renter_service: Annotated[RenterService, Depends(get_renter_service)],
):
    """Creates a new renter. Accepts property_id in the payload."""
    renter = renter_service.create_renter(data, owner_id=current_user["user_id"])
    return renter


@router.patch("/{renter_id}", response_model=RenterRead)
def update_renter(
    renter_id: int,
    data: RenterUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
    renter_service: Annotated[RenterService, Depends(get_renter_service)],
):
    """Partially updates a renter (edits, moving properties, or renewing leases)."""
    renter = renter_service.update_renter(renter_id, data, owner_id=current_user["user_id"])
    if renter is None:
        raise HTTPException(status_code=404, detail="Renter not found")
    return renter


@router.delete("/{renter_id}", status_code=204)
def delete_renter(
    renter_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    renter_service: Annotated[RenterService, Depends(get_renter_service)],
):
    """Deletes a renter from the database entirely."""
    deleted = renter_service.delete_renter(renter_id, owner_id=current_user["user_id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Renter not found")
    return None

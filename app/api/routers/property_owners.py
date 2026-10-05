from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.dependencies import get_current_user, get_property_owner_service
from app.schemas.property_owner import (
    PropertyOwnerCreate,
    PropertyOwnerRead,
    PropertyOwnerUpdate,
)
from app.services.property_owner_service import PropertyOwnerService

router = APIRouter()

_NOT_FOUND = "Property owner not found"


@router.get("", response_model=list[PropertyOwnerRead])
def list_property_owners(
    current_user: Annotated[dict, Depends(get_current_user)],
    service: Annotated[PropertyOwnerService, Depends(get_property_owner_service)],
    q: str | None = Query(None),
    include_inactive: bool = Query(False),
):
    """List the human owners of the account's properties, optionally searched."""
    return service.list_owners(current_user["user_id"], q=q, include_inactive=include_inactive)


@router.get("/{property_owner_id}", response_model=PropertyOwnerRead)
def get_property_owner(
    property_owner_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: Annotated[PropertyOwnerService, Depends(get_property_owner_service)],
):
    owner = service.get_owner(property_owner_id, current_user["user_id"])
    if owner is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return owner


@router.post("", response_model=PropertyOwnerRead, status_code=201)
def create_property_owner(
    data: PropertyOwnerCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: Annotated[PropertyOwnerService, Depends(get_property_owner_service)],
):
    return service.create_owner(data, current_user["user_id"])


@router.patch("/{property_owner_id}", response_model=PropertyOwnerRead)
def update_property_owner(
    property_owner_id: int,
    data: PropertyOwnerUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: Annotated[PropertyOwnerService, Depends(get_property_owner_service)],
):
    owner = service.update_owner(property_owner_id, data, current_user["user_id"])
    if owner is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return owner


@router.delete("/{property_owner_id}", status_code=204)
def delete_property_owner(
    property_owner_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: Annotated[PropertyOwnerService, Depends(get_property_owner_service)],
):
    """Delete an owner with no properties left; 409 while any property points at them."""
    if not service.delete_owner(property_owner_id, current_user["user_id"]):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return Response(status_code=204)

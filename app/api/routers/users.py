from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import (
    get_current_owner,
    get_current_user,
    get_owner_repository,
    get_user_service,
)
from app.repositories.owner_repository import OwnerRepository
from app.schemas.owner import OwnerRead
from app.services.user_service import UserService

router = APIRouter()


@router.get("/me", response_model=OwnerRead)
def get_my_profile(
    current_user: Annotated[dict, Depends(get_current_owner)],
    owner_repository: Annotated[OwnerRepository, Depends(get_owner_repository)],
):
    """Returns the authenticated owner's profile (synced from the Firebase token)."""
    owner = owner_repository.get(current_user["user_id"])
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Owner profile not found")
    return owner


@router.delete("/me", status_code=200)
def delete_my_account(
    current_user: Annotated[dict, Depends(get_current_user)],
    user_service: Annotated[UserService, Depends(get_user_service)],
):
    """Deletes the authenticated user's account and all associated data."""
    try:
        user_service.delete_account(owner_id=current_user["user_id"])
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Account deletion failed: {exc}",
        )
    return {"success": True}

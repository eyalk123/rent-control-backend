from datetime import date
from typing import Annotated

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_current_owner,
    get_current_user,
    get_owner_repository,
    get_user_service,
)
from app.database import get_db
from app.repositories.owner_repository import OwnerRepository
from app.schemas.owner import OwnerRead
from app.schemas.tour_state import TourStateRead, TourStateUpdate
from app.services.export_service import build_export_zip
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


@router.get("/me/tour-state", response_model=TourStateRead)
def get_my_tour_state(
    current_user: Annotated[dict, Depends(get_current_owner)],
    owner_repository: Annotated[OwnerRepository, Depends(get_owner_repository)],
):
    """Which onboarding tours and seeds this owner has already been shown.

    Never 404s: an owner with no row yet has simply seen nothing, and the clients must be
    able to ask this on first launch without special-casing the answer.
    """
    return TourStateRead(**owner_repository.get_tour_state(current_user["user_id"]))


@router.patch("/me/tour-state", response_model=TourStateRead)
def update_my_tour_state(
    payload: TourStateUpdate,
    current_user: Annotated[dict, Depends(get_current_owner)],
    owner_repository: Annotated[OwnerRepository, Depends(get_owner_repository)],
):
    """Records that a tour finished or a seed was shown. Merges — see the schema for why."""
    state = owner_repository.merge_tour_state(
        current_user["user_id"],
        tours_seen=payload.tours_seen,
        seeds_shown=payload.seeds_shown,
        tours_disabled=payload.tours_disabled,
        reset=payload.reset,
    )
    return TourStateRead(**state)


@router.get("/me/export")
def export_my_data(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Everything the owner owns, as a ZIP: one .xlsx workbook plus their uploaded files."""
    try:
        content = build_export_zip(db, current_user["user_id"])
    except Exception as exc:
        # Reported rather than echoed: the raw exception text could carry row data.
        sentry_sdk.capture_exception(exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Export failed. Please try again.",
        )

    filename = f"rent-control-export-{date.today().isoformat()}.zip"
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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

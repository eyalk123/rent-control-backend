from datetime import date
from typing import Annotated

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_current_owner,
    get_current_user,
    get_legal_acceptance_repository,
    get_owner_repository,
    get_user_service,
)
from app.config import settings
from app.database import get_db
from app.models.legal_acceptance import LegalDocumentEnum
from app.repositories.legal_acceptance_repository import LegalAcceptanceRepository
from app.repositories.owner_repository import OwnerRepository
from app.schemas.legal_acceptance import (
    LegalAcceptanceRead,
    LegalAcceptanceRecord,
    LegalStatusRead,
)
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


def _legal_status(repository: LegalAcceptanceRepository, owner_id: str) -> LegalStatusRead:
    latest = repository.latest_per_document(owner_id)
    return LegalStatusRead(
        terms=_read_or_none(latest.get(LegalDocumentEnum.TERMS)),
        privacy=_read_or_none(latest.get(LegalDocumentEnum.PRIVACY)),
        required_terms_version=settings.CURRENT_TERMS_VERSION,
        required_privacy_version=settings.CURRENT_PRIVACY_VERSION,
    )


def _read_or_none(acceptance) -> LegalAcceptanceRead | None:
    return LegalAcceptanceRead.model_validate(acceptance) if acceptance is not None else None


@router.get("/me/legal", response_model=LegalStatusRead)
def get_my_legal_status(
    current_user: Annotated[dict, Depends(get_current_owner)],
    legal_repository: Annotated[
        LegalAcceptanceRepository, Depends(get_legal_acceptance_repository)
    ],
):
    """Which version of each legal document this owner last accepted, and which this
    server considers current.

    Authenticated like every other /me route, and it must stay that way even though its
    job is to be asked *before* the client will show the app: the answer is one owner's
    acceptance history, which is theirs alone to read.

    Never 404s. Every account created before this table existed has accepted nothing, and
    "nothing" is a real answer the clients must be able to act on rather than an error.
    """
    return _legal_status(legal_repository, current_user["user_id"])


@router.post("/me/legal", response_model=LegalStatusRead, status_code=status.HTTP_201_CREATED)
def record_my_legal_acceptance(
    payload: LegalAcceptanceRecord,
    current_user: Annotated[dict, Depends(get_current_owner)],
    legal_repository: Annotated[
        LegalAcceptanceRepository, Depends(get_legal_acceptance_repository)
    ],
):
    """Records that this owner accepted the given documents at the given versions.

    The versions come from the client because the client is what displayed them — see
    `LegalAcceptance.version`. One request carries both documents, since the UI asks for
    them with a single tick.
    """
    for item in payload.acceptances:
        legal_repository.record(
            owner_id=current_user["user_id"],
            document=item.document,
            version=item.version,
            locale=item.locale,
            platform=payload.platform,
        )
    return _legal_status(legal_repository, current_user["user_id"])


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

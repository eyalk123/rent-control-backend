"""The tax tracks report and its household groups. Web only, and only where the account's
country has the report (``capabilities.tax_tracks`` with tax figures); 404 elsewhere."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_entitlement_gate, get_tax_tracks_service
from app.database import get_db
from app.repositories.owner_repository import OwnerRepository, owner_today
from app.services.entitlement_gate import EntitlementGate
from app.services.tax_tracks_service import TaxTracksService

router = APIRouter()


class TaxGroupWrite(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    property_owner_ids: list[int] = []
    property_ids: list[int] = []

    @field_validator("name", mode="before")
    @classmethod
    def trim(cls, v):
        return v.strip() if isinstance(v, str) else v


class TaxYearInputWrite(BaseModel):
    group_key: str = Field(..., max_length=40)
    year: int = Field(..., ge=2000, le=2100)
    # Annual, as typed. None clears the answer.
    other_income: float | None = Field(None, ge=0, le=1_000_000_000)
    credit_points: float | None = Field(None, ge=0, le=30)


def _account_country(db: Session, owner_id: str) -> str | None:
    owner = OwnerRepository(db).get(owner_id)
    return owner.country if owner else None


@router.get("/report")
def tax_report(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[TaxTracksService, Depends(get_tax_tracks_service)],
    gate: Annotated[EntitlementGate, Depends(get_entitlement_gate)],
    year: int = Query(..., ge=2000, le=2100),
):
    owner_id = current_user["user_id"]
    return service.report(
        owner_id,
        _account_country(db, owner_id),
        year,
        owner_today(db, owner_id),
        gate.hidden_property_ids(owner_id),
    )


@router.put("/inputs")
def save_inputs(
    data: TaxYearInputWrite,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[TaxTracksService, Depends(get_tax_tracks_service)],
):
    """Other income and credit points for one group's tax year."""
    owner_id = current_user["user_id"]
    _, first_year = service.country_for(_account_country(db, owner_id))
    return service.save_inputs(
        owner_id,
        data.group_key,
        data.year,
        first_year,
        owner_today(db, owner_id),
        data.other_income,
        data.credit_points,
    )


@router.get("/groups")
def list_groups(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[TaxTracksService, Depends(get_tax_tracks_service)],
):
    owner_id = current_user["user_id"]
    service.country_for(_account_country(db, owner_id))
    return service.list_groups(owner_id)


@router.post("/groups", status_code=201)
def create_group(
    data: TaxGroupWrite,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[TaxTracksService, Depends(get_tax_tracks_service)],
):
    owner_id = current_user["user_id"]
    service.country_for(_account_country(db, owner_id))
    return service.save_group(owner_id, data.name, data.property_owner_ids, data.property_ids)


@router.put("/groups/{group_id}")
def update_group(
    group_id: int,
    data: TaxGroupWrite,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[TaxTracksService, Depends(get_tax_tracks_service)],
):
    owner_id = current_user["user_id"]
    service.country_for(_account_country(db, owner_id))
    return service.save_group(
        owner_id, data.name, data.property_owner_ids, data.property_ids, group_id=group_id
    )


@router.delete("/groups/{group_id}", status_code=204)
def delete_group(
    group_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: Annotated[TaxTracksService, Depends(get_tax_tracks_service)],
):
    if not service.delete_group(current_user["user_id"], group_id):
        raise HTTPException(status_code=404, detail="Tax group not found")
    return Response(status_code=204)

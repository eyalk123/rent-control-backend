"""The tax tracks report. Web only, and only where the account's country has the report
(``capabilities.tax_tracks`` with tax figures); 404 elsewhere."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_entitlement_gate, get_tax_tracks_service
from app.database import get_db
from app.repositories.owner_repository import OwnerRepository, owner_today
from app.services.entitlement_gate import EntitlementGate
from app.services.tax_tracks_service import TaxTracksService

router = APIRouter()


class TaxYearInputWrite(BaseModel):
    # ``owner:<id>``, or ``none`` for the properties with no owner.
    person_key: str = Field(..., max_length=40, pattern=r"^(owner:\d+|none)$")
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
    # The flats picked on the page, comma-separated. None picked: only the list to pick from.
    property_ids: str = Query("", max_length=4000, pattern=r"^(\d+(,\d+)*)?$"),
):
    owner_id = current_user["user_id"]
    return service.report(
        owner_id,
        _account_country(db, owner_id),
        year,
        owner_today(db, owner_id),
        gate.hidden_property_ids(owner_id),
        [int(i) for i in property_ids.split(",") if i],
    )


@router.put("/inputs")
def save_inputs(
    data: TaxYearInputWrite,
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[TaxTracksService, Depends(get_tax_tracks_service)],
):
    """One person's other income and credit points for a tax year."""
    owner_id = current_user["user_id"]
    _, first_year = service.country_for(_account_country(db, owner_id))
    return service.save_inputs(
        owner_id,
        data.person_key,
        data.year,
        first_year,
        owner_today(db, owner_id),
        data.other_income,
        data.credit_points,
    )

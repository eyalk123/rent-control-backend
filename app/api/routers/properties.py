from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_current_user,
    get_mortgage_service,
    get_property_file_repository,
    get_property_service,
)
from app.api.routers.reports import _owner_formats
from app.database import get_db
from app.repositories.property_file_repository import PropertyFileRepository
from app.schemas.property import PropertyCreate, PropertyRead, PropertyUpdate
from app.schemas.property_file import PropertyFileCreate, PropertyFileRead
from app.schemas.property_mortgage import (
    MortgageCreate,
    MortgagePaymentCreate,
    MortgageRead,
    MortgageUpdate,
)
from app.schemas.property_valuation import PropertyValuationCreate, PropertyValuationRead
from app.schemas.renter import PropertyRenterSummary
from app.services.firebase_storage import release_file_urls
from app.services.mortgage_service import MortgageService
from app.services.property_service import PropertyService
from app.services.property_sheet_service import SheetFormats, generate_property_sheet_pdf
from app.repositories.owner_repository import owner_today

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
    include_ended: bool = False,
):
    """Returns renters linked to the property for e.g. the add-revenue form. Active
    leases only unless `include_ended` is set — see the service for why that matters."""
    renters = property_service.get_property_renters(
        property_id, owner_id=current_user["user_id"], include_ended=include_ended
    )
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


@router.get("/{property_id}/sheet")
def property_sheet(
    property_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    property_service: Annotated[PropertyService, Depends(get_property_service)],
    db: Annotated[Session, Depends(get_db)],
    lang: str = Query("en", pattern="^(en|he)$"),
):
    """A one-page PDF of the property for a new renter — renter-safe fields only (see
    `property_sheet_service`), in the language the app is in."""
    owner_id = current_user["user_id"]
    property = property_service.get_property(property_id, owner_id=owner_id)
    if property is None:
        raise HTTPException(status_code=404, detail="Property not found")
    content = generate_property_sheet_pdf(
        property, owner_id, lang, SheetFormats(*_owner_formats(db, owner_id)),
        today=owner_today(db, owner_id),
    )
    return Response(
        content=content,
        media_type="application/pdf",
        # The id, not the address: a Hebrew address cannot go in a latin-1 header, and the
        # clients name the saved file themselves.
        headers={"Content-Disposition": f'attachment; filename="property-{property_id}.pdf"'},
    )


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


@router.get("/{property_id}/files", response_model=list[PropertyFileRead])
def list_property_files(
    property_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    property_service: Annotated[PropertyService, Depends(get_property_service)],
    file_repo: Annotated[PropertyFileRepository, Depends(get_property_file_repository)],
):
    if not property_service.get_property(property_id, owner_id=current_user["user_id"]):
        raise HTTPException(status_code=404, detail="Property not found")
    return file_repo.get_by_property(property_id)


@router.post("/{property_id}/files/bulk", response_model=list[PropertyFileRead], status_code=201)
def bulk_create_property_files(
    property_id: int,
    files: list[PropertyFileCreate],
    current_user: Annotated[dict, Depends(get_current_user)],
    property_service: Annotated[PropertyService, Depends(get_property_service)],
    file_repo: Annotated[PropertyFileRepository, Depends(get_property_file_repository)],
):
    if not property_service.get_property(property_id, owner_id=current_user["user_id"]):
        raise HTTPException(status_code=404, detail="Property not found")
    return file_repo.bulk_create(property_id, [f.model_dump() for f in files])


@router.delete("/{property_id}/files/{file_id}", status_code=204)
def delete_property_file(
    property_id: int,
    file_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    property_service: Annotated[PropertyService, Depends(get_property_service)],
    file_repo: Annotated[PropertyFileRepository, Depends(get_property_file_repository)],
):
    if not property_service.get_property(property_id, owner_id=current_user["user_id"]):
        raise HTTPException(status_code=404, detail="Property not found")
    file = file_repo.get_by_id(file_id, property_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    url = file.url
    file_repo.delete(file)
    release_file_urls(file_repo.session, current_user["user_id"], [url])
    return None


@router.get("/{property_id}/valuations", response_model=list[PropertyValuationRead])
def list_property_valuations(
    property_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    property_service: Annotated[PropertyService, Depends(get_property_service)],
):
    """The property's value history, newest first. The first entry is `current_value`."""
    valuations = property_service.list_valuations(property_id, owner_id=current_user["user_id"])
    if valuations is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return valuations


@router.post("/{property_id}/valuations", response_model=PropertyValuationRead, status_code=201)
def create_property_valuation(
    property_id: int,
    data: PropertyValuationCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    property_service: Annotated[PropertyService, Depends(get_property_service)],
):
    valuation = property_service.add_valuation(property_id, data, owner_id=current_user["user_id"])
    if valuation is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return valuation


@router.delete("/{property_id}/valuations/{valuation_id}", status_code=204)
def delete_property_valuation(
    property_id: int,
    valuation_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    property_service: Annotated[PropertyService, Depends(get_property_service)],
):
    if not property_service.delete_valuation(
        property_id, valuation_id, owner_id=current_user["user_id"]
    ):
        raise HTTPException(status_code=404, detail="Valuation not found")
    return None


# ── Mortgages (capability-gated: 422 where the property's country has none) ──


@router.get("/{property_id}/mortgages", response_model=list[MortgageRead])
def list_property_mortgages(
    property_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    mortgage_service: Annotated[MortgageService, Depends(get_mortgage_service)],
):
    mortgages = mortgage_service.list(property_id, owner_id=current_user["user_id"])
    if mortgages is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return mortgages


@router.post("/{property_id}/mortgages", response_model=MortgageRead, status_code=201)
def create_property_mortgage(
    property_id: int,
    data: MortgageCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    mortgage_service: Annotated[MortgageService, Depends(get_mortgage_service)],
):
    mortgage = mortgage_service.create(property_id, data, owner_id=current_user["user_id"])
    if mortgage is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return mortgage


@router.patch("/{property_id}/mortgages/{mortgage_id}", response_model=MortgageRead)
def update_property_mortgage(
    property_id: int,
    mortgage_id: int,
    data: MortgageUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
    mortgage_service: Annotated[MortgageService, Depends(get_mortgage_service)],
):
    mortgage = mortgage_service.update(
        property_id, mortgage_id, data, owner_id=current_user["user_id"]
    )
    if mortgage is None:
        raise HTTPException(status_code=404, detail="Mortgage not found")
    return mortgage


@router.delete("/{property_id}/mortgages/{mortgage_id}", status_code=204)
def delete_property_mortgage(
    property_id: int,
    mortgage_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    mortgage_service: Annotated[MortgageService, Depends(get_mortgage_service)],
):
    if not mortgage_service.delete(property_id, mortgage_id, owner_id=current_user["user_id"]):
        raise HTTPException(status_code=404, detail="Mortgage not found")
    return None


@router.post(
    "/{property_id}/mortgages/{mortgage_id}/payments",
    response_model=MortgageRead,
    status_code=201,
)
def create_mortgage_payment(
    property_id: int,
    mortgage_id: int,
    data: MortgagePaymentCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
    mortgage_service: Annotated[MortgageService, Depends(get_mortgage_service)],
):
    """A new monthly amount from a date on. Answers with the whole mortgage."""
    mortgage = mortgage_service.add_payment(
        property_id, mortgage_id, data, owner_id=current_user["user_id"]
    )
    if mortgage is None:
        raise HTTPException(status_code=404, detail="Mortgage not found")
    return mortgage


@router.delete(
    "/{property_id}/mortgages/{mortgage_id}/payments/{payment_id}", response_model=MortgageRead
)
def delete_mortgage_payment(
    property_id: int,
    mortgage_id: int,
    payment_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    mortgage_service: Annotated[MortgageService, Depends(get_mortgage_service)],
):
    """Answers with the mortgage as it now stands. The last amount cannot be removed (400)."""
    mortgage = mortgage_service.delete_payment(
        property_id, mortgage_id, payment_id, owner_id=current_user["user_id"]
    )
    if mortgage is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return mortgage

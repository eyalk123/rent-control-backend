from typing import Annotated, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_current_user,
    get_entitlement_gate,
    get_report_export_repository,
)
from app.database import get_db
from app.services import country_service
from app.models.report_export import ReportExport
from app.repositories.owner_repository import OwnerRepository
from app.repositories.report_export_repository import ReportExportRepository
from app.schemas.report import (
    ExpenseLogReportResponse,
    IncomeExpenseReportResponse,
    ReportExportRead,
)
from app.services.entitlement_gate import EntitlementGate
from app.services.report_service import (
    bundle_files,
    generate_expense_log_csv,
    generate_expense_log_pdf,
    generate_income_expense_csv,
    generate_income_expense_pdf,
    get_expense_log_data,
    get_income_expense_data,
    owner_file_part,
)

router = APIRouter()


def _owner_formats(db: Session, owner_id: str):
    """The currency, the thousands grouping and the symbol spacing a report prints with.

    Read from the owner rather than the reader's `?lang=`: a report is about a portfolio,
    and neither of these changes because someone switched the app to English. A missing
    owner row resolves to Israel, which is what every report did before this existed.

    The currency is resolved through ``effective_currency`` so an account that chose one
    other than its country's gets the one it chose — and, with it, the right side for the
    symbol. Grouping has no such override: it is the country's, always, because it is a way
    of writing numbers rather than a property of the money.

    All three come from one owner read, and all three are positional arguments of the two
    PDF generators in that order.
    """
    owner = OwnerRepository(db).get(owner_id)
    country = owner.country if owner is not None else None
    currency = country_service.effective_currency(
        country, owner.currency if owner is not None else None
    )
    config = country_service.config_for(country)
    return currency, config.number_format, config.currency_symbol_spaced


_MEDIA_TYPES = {"pdf": "application/pdf", "csv": "text/csv", "zip": "application/zip"}

# Repeated: `?owner=Dana&owner=Avi`. An empty value is the no-owner group; leaving the
# parameter out means every owner. Matched exactly as typed on the property.
OwnerQuery = Annotated[list[str] | None, Query()]


def _selected_owners(owner: list[str] | None) -> list[str] | None:
    return list(dict.fromkeys(owner)) if owner else None


def _file_response(content: bytes, kind: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=_MEDIA_TYPES[kind],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _render(
    build: Callable[[list[str] | None], tuple[bytes, IncomeExpenseReportResponse | ExpenseLogReportResponse]],
    owners: list[str] | None,
    split: bool,
    stem: str,
    format: str,
    lang: str,
) -> Response:
    """One file covering the selected owners, or — with ``split`` — a ZIP holding one file
    per owner, each generated on its own so its totals are that owner's alone.

    A selection with no data in the year still downloads, as one empty report, rather than
    an empty ZIP that looks like a failure.
    """
    content, data = build(owners)
    if not split:
        return _file_response(content, format, f"{stem}.{format}")
    files = [
        (f"{stem}-{owner_file_part(group.owner_name, lang)}.{format}", build([group.owner_name])[0])
        for group in data.owners
    ] or [(f"{stem}.{format}", content)]
    return _file_response(bundle_files(files), "zip", f"{stem}.zip")


@router.get("/income-expense")
def income_expense_report(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    repo: Annotated[ReportExportRepository, Depends(get_report_export_repository)],
    gate: Annotated[EntitlementGate, Depends(get_entitlement_gate)],
    year: int = Query(..., ge=2000, le=2100),
    format: str = Query("pdf", pattern="^(pdf|csv)$"),
    lang: str = Query("en", pattern="^(en|he)$"),
    # Chosen per report, next to the language, rather than stored on the account — a stored
    # preference would silently re-interpret history. Defaults to accrual, so a caller that
    # does not pass it gets exactly what this endpoint always returned.
    basis: str = Query("accrual", pattern="^(accrual|cash)$"),
    owner: OwnerQuery = None,
    split: bool = False,
):
    owner_id = current_user["user_id"]
    owners = _selected_owners(owner)
    hidden = gate.hidden_property_ids(owner_id)
    formats = _owner_formats(db, owner_id) if format == "pdf" else None

    def build(selection):
        data = get_income_expense_data(
            db, owner_id, year, basis, exclude_property_ids=hidden, owners=selection,
        )
        if format == "csv":
            return generate_income_expense_csv(data, lang).encode("utf-8-sig"), data
        return generate_income_expense_pdf(data, lang, *formats), data

    response = _render(build, owners, split, f"income-expense-{year}", format, lang)
    repo.create(ReportExport(
        owner_id=owner_id, report_type="income_expense", year=year, format=format,
        revenue_basis=basis, owners=owners, split_by_owner=split,
    ))
    return response


@router.get("/expense-log")
def expense_log_report(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    repo: Annotated[ReportExportRepository, Depends(get_report_export_repository)],
    gate: Annotated[EntitlementGate, Depends(get_entitlement_gate)],
    year: int = Query(..., ge=2000, le=2100),
    format: str = Query("pdf", pattern="^(pdf|csv)$"),
    lang: str = Query("en", pattern="^(en|he)$"),
    owner: OwnerQuery = None,
    split: bool = False,
):
    owner_id = current_user["user_id"]
    owners = _selected_owners(owner)
    hidden = gate.hidden_property_ids(owner_id)
    formats = _owner_formats(db, owner_id) if format == "pdf" else None

    def build(selection):
        data = get_expense_log_data(
            db, owner_id, year, lang, exclude_property_ids=hidden, owners=selection,
        )
        if format == "csv":
            return generate_expense_log_csv(data, lang).encode("utf-8-sig"), data
        return generate_expense_log_pdf(data, lang, *formats), data

    response = _render(build, owners, split, f"expense-log-{year}", format, lang)
    repo.create(ReportExport(
        owner_id=owner_id, report_type="expense_log", year=year, format=format,
        owners=owners, split_by_owner=split,
    ))
    return response


@router.get("/history", response_model=list[ReportExportRead])
def get_report_history(
    current_user: Annotated[dict, Depends(get_current_user)],
    repo: Annotated[ReportExportRepository, Depends(get_report_export_repository)],
):
    return repo.get_all_for_owner(current_user["user_id"])


@router.delete("/history/{export_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report_history(
    export_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    repo: Annotated[ReportExportRepository, Depends(get_report_export_repository)],
):
    export = repo.get_by_id_and_owner(export_id, current_user["user_id"])
    if not export:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export record not found")
    repo.delete(export)

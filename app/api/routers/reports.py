from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_report_export_repository
from app.database import get_db
from app.services import country_service
from app.models.report_export import ReportExport
from app.repositories.owner_repository import OwnerRepository
from app.repositories.report_export_repository import ReportExportRepository
from app.schemas.report import ReportExportRead
from app.services.report_service import (
    generate_expense_log_csv,
    generate_expense_log_pdf,
    generate_income_expense_csv,
    generate_income_expense_pdf,
    get_expense_log_data,
    get_income_expense_data,
)

router = APIRouter()


def _owner_formats(db: Session, owner_id: str):
    """The currency and the thousands grouping a report should print its amounts in.

    Read from the owner rather than the reader's `?lang=`: a report is about a portfolio,
    and neither of these changes because someone switched the app to English. A missing
    owner row resolves to Israel, which is what every report did before this existed.

    The currency is resolved through ``effective_currency`` so an account that chose one
    other than its country's gets the one it chose — and, with it, the right side for the
    symbol. Grouping has no such override: it is the country's, always, because it is a way
    of writing numbers rather than a property of the money.

    Both come from one owner read, and both are positional arguments of the two PDF
    generators in that order.
    """
    owner = OwnerRepository(db).get(owner_id)
    country = owner.country if owner is not None else None
    currency = country_service.effective_currency(
        country, owner.currency if owner is not None else None
    )
    return currency, country_service.config_for(country).number_format


@router.get("/income-expense")
def income_expense_report(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    repo: Annotated[ReportExportRepository, Depends(get_report_export_repository)],
    year: int = Query(..., ge=2000, le=2100),
    format: str = Query("pdf", pattern="^(pdf|csv)$"),
    lang: str = Query("en", pattern="^(en|he)$"),
    # Chosen per report, next to the language, rather than stored on the account — a stored
    # preference would silently re-interpret history. Defaults to accrual, so a caller that
    # does not pass it gets exactly what this endpoint always returned.
    basis: str = Query("accrual", pattern="^(accrual|cash)$"),
):
    data = get_income_expense_data(db, current_user["user_id"], year, basis)

    if format == "csv":
        content = generate_income_expense_csv(data, lang).encode("utf-8-sig")
        repo.create(ReportExport(owner_id=current_user["user_id"], report_type="income_expense", year=year, format="csv", revenue_basis=basis))
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="income-expense-{year}.csv"'},
        )

    content = generate_income_expense_pdf(data, lang, *_owner_formats(db, current_user["user_id"]))
    repo.create(ReportExport(owner_id=current_user["user_id"], report_type="income_expense", year=year, format="pdf", revenue_basis=basis))
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="income-expense-{year}.pdf"'},
    )


@router.get("/expense-log")
def expense_log_report(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    repo: Annotated[ReportExportRepository, Depends(get_report_export_repository)],
    year: int = Query(..., ge=2000, le=2100),
    format: str = Query("pdf", pattern="^(pdf|csv)$"),
    lang: str = Query("en", pattern="^(en|he)$"),
):
    data = get_expense_log_data(db, current_user["user_id"], year, lang)

    if format == "csv":
        content = generate_expense_log_csv(data, lang).encode("utf-8-sig")
        repo.create(ReportExport(owner_id=current_user["user_id"], report_type="expense_log", year=year, format="csv"))
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="expense-log-{year}.csv"'},
        )

    content = generate_expense_log_pdf(data, lang, *_owner_formats(db, current_user["user_id"]))
    repo.create(ReportExport(owner_id=current_user["user_id"], report_type="expense_log", year=year, format="pdf"))
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="expense-log-{year}.pdf"'},
    )


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

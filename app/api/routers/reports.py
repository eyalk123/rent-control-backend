from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_report_export_repository
from app.database import get_db
from app.models.report_export import ReportExport
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


@router.get("/income-expense")
def income_expense_report(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    repo: Annotated[ReportExportRepository, Depends(get_report_export_repository)],
    year: int = Query(..., ge=2000, le=2100),
    format: str = Query("pdf", pattern="^(pdf|csv)$"),
):
    data = get_income_expense_data(db, current_user["user_id"], year)

    if format == "csv":
        content = generate_income_expense_csv(data).encode("utf-8-sig")
        repo.create(ReportExport(owner_id=current_user["user_id"], report_type="income_expense", year=year, format="csv"))
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="income-expense-{year}.csv"'},
        )

    content = generate_income_expense_pdf(data)
    repo.create(ReportExport(owner_id=current_user["user_id"], report_type="income_expense", year=year, format="pdf"))
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
):
    data = get_expense_log_data(db, current_user["user_id"], year)

    if format == "csv":
        content = generate_expense_log_csv(data).encode("utf-8-sig")
        repo.create(ReportExport(owner_id=current_user["user_id"], report_type="expense_log", year=year, format="csv"))
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="expense-log-{year}.csv"'},
        )

    content = generate_expense_log_pdf(data)
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

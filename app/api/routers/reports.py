from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database import get_db
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
    year: int = Query(..., ge=2000, le=2100),
    format: str = Query("pdf", pattern="^(pdf|csv)$"),
):
    data = get_income_expense_data(db, current_user["user_id"], year)

    if format == "csv":
        content = generate_income_expense_csv(data).encode("utf-8-sig")
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="income-expense-{year}.csv"'},
        )

    content = generate_income_expense_pdf(data)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="income-expense-{year}.pdf"'},
    )


@router.get("/expense-log")
def expense_log_report(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    year: int = Query(..., ge=2000, le=2100),
    format: str = Query("pdf", pattern="^(pdf|csv)$"),
):
    data = get_expense_log_data(db, current_user["user_id"], year)

    if format == "csv":
        content = generate_expense_log_csv(data).encode("utf-8-sig")
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="expense-log-{year}.csv"'},
        )

    content = generate_expense_log_pdf(data)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="expense-log-{year}.pdf"'},
    )

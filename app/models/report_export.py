from sqlalchemy import Column, DateTime, Enum, Integer, String

from app.clock import utc_now_naive
from app.models.base import Base


class ReportExport(Base):
    __tablename__ = "report_exports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False)
    report_type = Column(Enum("income_expense", "expense_log", name="reporttype"), nullable=False)
    year = Column(Integer, nullable=False)
    format = Column(Enum("pdf", "csv", name="reportformat"), nullable=False)
    # Which revenue recognition basis produced it. NULL means accrual — every export that
    # predates the choice was generated on the only basis that existed. Recorded so the
    # history list can tell two otherwise-identical reports apart.
    revenue_basis = Column(String(16), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

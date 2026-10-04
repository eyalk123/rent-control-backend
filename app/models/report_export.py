from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, JSON, String, text

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
    # The property owners it was limited to, as typed on the properties ("" is the no-owner
    # group). NULL means every owner — and is what every export before the choice was.
    # Recorded so re-exporting from history reproduces the same report, not the whole
    # portfolio.
    owners = Column(JSON, nullable=True)
    # Downloaded as a ZIP with one file per owner rather than one grouped file.
    split_by_owner = Column(Boolean, nullable=False, server_default=text("false"), default=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

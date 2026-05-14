from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, Integer, String

from app.models.base import Base


class ReportExport(Base):
    __tablename__ = "report_exports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False)
    report_type = Column(Enum("income_expense", "expense_log", name="reporttype"), nullable=False)
    year = Column(Integer, nullable=False)
    format = Column(Enum("pdf", "csv", name="reportformat"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

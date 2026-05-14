from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.report_export import ReportExport


class ReportExportRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, export: ReportExport) -> ReportExport:
        self.session.add(export)
        self.session.commit()
        self.session.refresh(export)
        return export

    def get_all_for_owner(self, owner_id: str) -> list[ReportExport]:
        stmt = (
            select(ReportExport)
            .where(ReportExport.owner_id == owner_id)
            .order_by(ReportExport.created_at.desc())
        )
        return list(self.session.scalars(stmt).all())

    def get_by_id_and_owner(self, export_id: int, owner_id: str) -> ReportExport | None:
        stmt = select(ReportExport).where(
            ReportExport.id == export_id,
            ReportExport.owner_id == owner_id,
        )
        return self.session.scalar(stmt)

    def delete(self, export: ReportExport) -> None:
        self.session.delete(export)
        self.session.commit()

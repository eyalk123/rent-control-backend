from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.property_file import PropertyFile


class PropertyFileRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_property(self, property_id: int) -> list[PropertyFile]:
        stmt = select(PropertyFile).where(PropertyFile.property_id == property_id).order_by(PropertyFile.created_at)
        return list(self.session.scalars(stmt).all())

    def bulk_create(self, property_id: int, files: list[dict]) -> list[PropertyFile]:
        records = [PropertyFile(property_id=property_id, url=f["url"], label=f["label"]) for f in files]
        self.session.add_all(records)
        self.session.commit()
        for r in records:
            self.session.refresh(r)
        return records

    def get_by_id(self, file_id: int, property_id: int) -> PropertyFile | None:
        stmt = select(PropertyFile).where(
            PropertyFile.id == file_id,
            PropertyFile.property_id == property_id,
        )
        return self.session.scalar(stmt)

    def delete(self, file: PropertyFile) -> None:
        self.session.delete(file)
        self.session.commit()

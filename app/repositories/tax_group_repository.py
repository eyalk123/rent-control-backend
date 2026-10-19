import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tax_group import TaxGroup


class TaxGroupRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_all(self, owner_id: str) -> list[TaxGroup]:
        stmt = select(TaxGroup).where(TaxGroup.owner_id == owner_id).order_by(TaxGroup.id)
        return list(self.session.scalars(stmt).all())

    def get_by_id(self, group_id: int, owner_id: str) -> TaxGroup | None:
        stmt = select(TaxGroup).where(TaxGroup.id == group_id, TaxGroup.owner_id == owner_id)
        return self.session.scalar(stmt)

    def save(
        self,
        group: TaxGroup,
        name: str,
        property_owner_ids: list[int],
        property_ids: list[int],
    ) -> TaxGroup:
        group.name = name
        group.property_owner_ids = json.dumps(sorted(set(property_owner_ids)))
        group.property_ids = json.dumps(sorted(set(property_ids)))
        self.session.add(group)
        self.session.commit()
        self.session.refresh(group)
        return group

    def delete(self, group: TaxGroup) -> None:
        self.session.delete(group)
        self.session.commit()


def member_ids(raw: str | None) -> list[int]:
    """A stored id list, tolerating a malformed value as empty."""
    try:
        values = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return [v for v in values if isinstance(v, int)] if isinstance(values, list) else []

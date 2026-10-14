from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.property_valuation import PropertyValuation


class PropertyValuationRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_property(self, property_id: int) -> list[PropertyValuation]:
        """Newest first — the same order as `Property.valuations`."""
        stmt = (
            select(PropertyValuation)
            .where(PropertyValuation.property_id == property_id)
            .order_by(PropertyValuation.valued_on.desc(), PropertyValuation.id.desc())
        )
        return list(self.session.scalars(stmt).all())

    def get_by_id(self, valuation_id: int, property_id: int) -> PropertyValuation | None:
        stmt = select(PropertyValuation).where(
            PropertyValuation.id == valuation_id,
            PropertyValuation.property_id == property_id,
        )
        return self.session.scalar(stmt)

    def create(self, valuation: PropertyValuation) -> PropertyValuation:
        self.session.add(valuation)
        self.session.commit()
        self.session.refresh(valuation)
        return valuation

    def delete(self, valuation: PropertyValuation) -> None:
        self.session.delete(valuation)
        self.session.commit()

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.property_mortgage import MortgagePayment, PropertyMortgage


class PropertyMortgageRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_property(self, property_id: int) -> list[PropertyMortgage]:
        stmt = (
            select(PropertyMortgage)
            .where(PropertyMortgage.property_id == property_id)
            .order_by(PropertyMortgage.id)
        )
        return list(self.session.scalars(stmt).all())

    def get_by_id(self, mortgage_id: int, property_id: int) -> PropertyMortgage | None:
        stmt = select(PropertyMortgage).where(
            PropertyMortgage.id == mortgage_id,
            PropertyMortgage.property_id == property_id,
        )
        return self.session.scalar(stmt)

    def get_payment(self, payment_id: int, mortgage_id: int) -> MortgagePayment | None:
        stmt = select(MortgagePayment).where(
            MortgagePayment.id == payment_id,
            MortgagePayment.mortgage_id == mortgage_id,
        )
        return self.session.scalar(stmt)

    def save(self, mortgage: PropertyMortgage) -> PropertyMortgage:
        """Commit the mortgage with any payments added to it, and reload them in order."""
        self.session.add(mortgage)
        self.session.commit()
        self.session.refresh(mortgage)
        self.session.expire(mortgage, ["payments"])
        return mortgage

    def delete(self, obj: PropertyMortgage | MortgagePayment) -> None:
        self.session.delete(obj)
        self.session.commit()

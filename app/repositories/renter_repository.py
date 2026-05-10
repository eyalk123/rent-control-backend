from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.property import Property
from app.models.renter import Renter
from app.models.transaction import Transaction


class RenterRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_all(self, owner_id: str | None = None) -> list[Renter]:
        stmt = select(Renter).options(selectinload(Renter.property))
        if owner_id is not None:
            stmt = stmt.where(Renter.owner_id == owner_id)
        return list(self.session.scalars(stmt).all())

    def get_by_id(self, renter_id: int) -> Renter | None:
        stmt = (
            select(Renter)
            .where(Renter.id == renter_id)
            .options(selectinload(Renter.property))
        )
        return self.session.scalar(stmt)

    def create(self, renter: Renter) -> Renter:
        self.session.add(renter)
        self.session.commit()
        self.session.refresh(renter)
        return renter

    def update(self, renter: Renter, data: dict) -> Renter:
        nullable_fields = {
            "property_id",
            "number_of_payments",
            "payment_type",
            "payment_day_of_month",
            "insurance_type",
            "insurance_amount",
            "contact_id",
            "extra_contacts",
            "full_contract_url",
            "id_image_url",
        }
        always_set_fields = {"lease_years", "lease_end"}
        for key, value in data.items():
            if hasattr(renter, key) and (
                value is not None or key in nullable_fields or key in always_set_fields
            ):
                setattr(renter, key, value)
        self.session.commit()
        self.session.refresh(renter)
        return renter

    def delete(self, renter_id: int) -> bool:
        renter = self.get_by_id(renter_id)
        if renter is None:
            return False
        self.delete_obj(renter)
        return True

    def delete_obj(self, renter: Renter) -> None:
        self.session.delete(renter)
        self.session.commit()

    def get_overdue_this_month(
        self,
        owner_id: str,
        property_owner: str | None = None,
    ) -> list[Renter]:
        today = date.today()
        first_of_month = date(today.year, today.month, 1)
        if today.month == 12:
            first_of_next_month = date(today.year + 1, 1, 1)
        else:
            first_of_next_month = date(today.year, today.month + 1, 1)

        paid_subq = (
            select(Transaction.renter_id)
            .where(
                Transaction.type == "revenue",
                Transaction.owner_id == owner_id,
                Transaction.month_for >= first_of_month,
                Transaction.month_for < first_of_next_month,
                Transaction.renter_id.isnot(None),
            )
            .distinct()
            .scalar_subquery()
        )

        stmt = (
            select(Renter)
            .join(Property, Renter.property_id == Property.id)
            .options(selectinload(Renter.property))
            .where(
                Renter.owner_id == owner_id,
                Renter.lease_start <= today,
                Renter.lease_end >= today,
                Renter.payment_day_of_month.isnot(None),
                Renter.payment_day_of_month <= today.day,
                Renter.id.notin_(paid_subq),
            )
        )
        if property_owner is not None:
            stmt = stmt.where(Property.property_owner == property_owner)

        return list(self.session.scalars(stmt).all())

    def get_by_property_id(
        self,
        property_id: int,
        owner_id: str,
        active_only: bool = True,
    ) -> list[Renter]:
        today = date.today()
        stmt = (
            select(Renter)
            .join(Property, Renter.property_id == Property.id)
            .where(
                Renter.property_id == property_id,
                Property.owner_id == owner_id,
            )
        )
        if active_only:
            stmt = stmt.where(
                Renter.lease_start <= today,
                Renter.lease_end >= today,
            )
        stmt = stmt.order_by(Renter.lease_start.desc())
        return list(self.session.scalars(stmt).all())

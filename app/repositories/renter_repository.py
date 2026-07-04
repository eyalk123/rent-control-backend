from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.property import Property
from app.models.renter import Renter
from app.models.transaction import Transaction


def _scope_conditions(
    property_ids: list[int] | None,
    property_owners: list[str] | None,
    renter_ids: list[int] | None,
) -> list:
    """Build the positive-only, union scope filter shared by the overdue and
    expiring queries. Returns a list to splat into ``.where(*conds)`` — empty
    when no scope is given (matches all)."""
    clauses = []
    if property_ids:
        clauses.append(Renter.property_id.in_(property_ids))
    if property_owners:
        clauses.append(Property.property_owner.in_(property_owners))
    if renter_ids:
        clauses.append(Renter.id.in_(renter_ids))
    return [or_(*clauses)] if clauses else []


class RenterRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_distinct_owner_ids(self) -> list[str]:
        """Every owner that has at least one renter — the set the reminder job
        evaluates (vs. only owners with a registered push device)."""
        stmt = select(Renter.owner_id).where(Renter.owner_id.isnot(None)).distinct()
        return list(self.session.scalars(stmt).all())

    def get_by_escalation_mode(self, mode: str) -> list[Renter]:
        """Every renter using a given rent-escalation mode, across all owners — the
        set the CPI indexing job recomputes."""
        stmt = select(Renter).where(Renter.rent_escalation_mode == mode)
        return list(self.session.scalars(stmt).all())

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
            "contract_term_years",
            "option_years",
            "base_rent",
            "rent_escalation_mode",
            "rent_escalation_value",
            "cpi_base_index",
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
        property_ids: list[int] | None = None,
        property_owners: list[str] | None = None,
        renter_ids: list[int] | None = None,
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
                # A missing payment day falls back to the 1st (backend-only; the
                # column itself stays null and is never shown to the user).
                func.coalesce(Renter.payment_day_of_month, 1) <= today.day,
                Renter.id.notin_(paid_subq),
                *_scope_conditions(property_ids, property_owners, renter_ids),
            )
        )

        return list(self.session.scalars(stmt).all())

    def get_expiring_leases(
        self,
        owner_id: str,
        days_until: int = 90,
        property_ids: list[int] | None = None,
        property_owners: list[str] | None = None,
        renter_ids: list[int] | None = None,
    ) -> list[Renter]:
        today = date.today()
        cutoff = today + timedelta(days=days_until)

        stmt = (
            select(Renter)
            .join(Property, Renter.property_id == Property.id)
            .options(selectinload(Renter.property))
            .where(
                Renter.owner_id == owner_id,
                Renter.lease_start <= today,
                Renter.lease_end > today,
                Renter.lease_end <= cutoff,
                *_scope_conditions(property_ids, property_owners, renter_ids),
            )
            .order_by(Renter.lease_end.asc())
        )
        return list(self.session.scalars(stmt).all())

    def get_active(
        self,
        owner_id: str,
        property_ids: list[int] | None = None,
        property_owners: list[str] | None = None,
        renter_ids: list[int] | None = None,
    ) -> list[Renter]:
        """Renters with a currently-active lease in scope — used by the rule
        preview to estimate how many renters a rule matches."""
        today = date.today()
        stmt = (
            select(Renter)
            .join(Property, Renter.property_id == Property.id)
            .options(selectinload(Renter.property))
            .where(
                Renter.owner_id == owner_id,
                Renter.lease_start <= today,
                Renter.lease_end >= today,
                *_scope_conditions(property_ids, property_owners, renter_ids),
            )
        )
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

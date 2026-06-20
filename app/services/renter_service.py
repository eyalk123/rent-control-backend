import json
from datetime import date

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException

from app.models.renter import Renter
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.schemas.renter import ExpiringRenterRead, OverdueRenterRead, RenterCreate, RenterUpdate


def _compute_lease_end(lease_start: date, lease_years_count: int) -> date:
    return lease_start + relativedelta(years=lease_years_count)


def _encode_lease_years(lease_years) -> str:
    return json.dumps([ly.model_dump() for ly in lease_years])


class RenterService:
    def __init__(
        self,
        renter_repository: RenterRepository,
        property_repository: PropertyRepository,
    ):
        self.renter_repository = renter_repository
        self.property_repository = property_repository

    def list_renters(self, owner_id: str):
        return self.renter_repository.get_all(owner_id=owner_id)

    def get_renter(self, renter_id: int, owner_id: str):
        renter = self.renter_repository.get_by_id(renter_id)
        if renter is None:
            return None
        self._check_renter_access(renter, owner_id)
        return renter

    def _check_renter_access(self, renter: Renter, owner_id: str) -> None:
        if renter.owner_id is not None and renter.owner_id != owner_id:
            raise HTTPException(status_code=403, detail="Access denied")

    def create_renter(self, data: RenterCreate, owner_id: str):
        if data.property_id is not None:
            property = self.property_repository.get_by_id(data.property_id, owner_id)
            if property is None:
                raise HTTPException(status_code=403, detail="Property not found or access denied")
        lease_end = _compute_lease_end(data.lease_start, len(data.lease_years)) if data.lease_start else None
        renter = Renter(
            owner_id=owner_id,
            property_id=data.property_id,
            first_name=data.first_name,
            last_name=data.last_name,
            phone=data.phone,
            email=data.email,
            lease_years=_encode_lease_years(data.lease_years),
            lease_start=data.lease_start,
            lease_end=lease_end,
            contract_term_years=data.contract_term_years,
            option_years=data.option_years,
            base_rent=data.base_rent,
            rent_escalation_mode=(
                data.rent_escalation_mode.value if data.rent_escalation_mode else None
            ),
            rent_escalation_value=data.rent_escalation_value,
            number_of_payments=data.number_of_payments,
            payment_type=data.payment_type,
            payment_day_of_month=data.payment_day_of_month,
            insurance_type=data.insurance_type,
            insurance_amount=data.insurance_amount,
            contact_id=data.contact_id,
            extra_contacts=[c.model_dump() for c in data.extra_contacts] if data.extra_contacts else None,
            full_contract_url=data.full_contract_url,
            id_image_url=data.id_image_url,
        )
        return self.renter_repository.create(renter)

    def update_renter(self, renter_id: int, data: RenterUpdate, owner_id: str):
        renter = self.renter_repository.get_by_id(renter_id)
        if renter is None:
            return None
        self._check_renter_access(renter, owner_id)
        update_dict = data.model_dump(exclude_unset=True)
        if "property_id" in update_dict and update_dict["property_id"] is not None:
            property = self.property_repository.get_by_id(
                update_dict["property_id"], owner_id
            )
            if property is None:
                raise HTTPException(status_code=403, detail="Property not found or access denied")
        if "lease_years" in update_dict:
            update_dict["lease_years"] = _encode_lease_years(data.lease_years)
        if "extra_contacts" in update_dict and update_dict["extra_contacts"] is not None:
            update_dict["extra_contacts"] = [c.model_dump() for c in data.extra_contacts]
        lease_start = update_dict.get("lease_start", renter.lease_start)
        if "lease_years" in update_dict or "lease_start" in update_dict:
            lease_years_raw = update_dict.get("lease_years")
            if lease_years_raw is not None:
                count = len(json.loads(lease_years_raw))
            else:
                count = len(json.loads(renter.lease_years))
            update_dict["lease_end"] = _compute_lease_end(lease_start, count) if lease_start else None
        return self.renter_repository.update(renter, update_dict)

    def get_overdue_this_month(
        self,
        owner_id: str,
        property_owner: str | None = None,
        property_ids: list[int] | None = None,
        property_owners: list[str] | None = None,
        renter_ids: list[int] | None = None,
    ) -> list[OverdueRenterRead]:
        import calendar

        today = date.today()
        # Back-compat: the existing /renters/overdue endpoint passes a single
        # property_owner; fold it into the multi-value scope filter.
        if property_owner is not None and not property_owners:
            property_owners = [property_owner]
        renters = self.renter_repository.get_overdue_this_month(
            owner_id=owner_id,
            property_ids=property_ids,
            property_owners=property_owners,
            renter_ids=renter_ids,
        )

        result = []
        for r in renters:
            last_day = calendar.monthrange(today.year, today.month)[1]
            pay_day = min(r.payment_day_of_month, last_day)
            expected = date(today.year, today.month, pay_day)
            days_overdue = (today - expected).days

            lease_data = json.loads(r.lease_years) if isinstance(r.lease_years, str) else (r.lease_years or [])
            monthly_amount = lease_data[0]["amount"] if lease_data else 0.0

            prop = r.property
            result.append(OverdueRenterRead(
                renter_id=r.id,
                first_name=r.first_name,
                last_name=r.last_name,
                property_id=r.property_id,
                property_address=prop.address if prop else None,
                property_city=prop.city if prop else None,
                property_owner=prop.property_owner if prop else None,
                monthly_amount=monthly_amount,
                payment_day_of_month=r.payment_day_of_month,
                payment_type=r.payment_type,
                days_overdue=days_overdue,
            ))
        return result

    def get_expiring_leases(
        self,
        owner_id: str,
        days_until: int = 90,
        property_ids: list[int] | None = None,
        property_owners: list[str] | None = None,
        renter_ids: list[int] | None = None,
    ) -> list[ExpiringRenterRead]:
        today = date.today()
        renters = self.renter_repository.get_expiring_leases(
            owner_id=owner_id,
            days_until=days_until,
            property_ids=property_ids,
            property_owners=property_owners,
            renter_ids=renter_ids,
        )
        result = []
        for r in renters:
            days_left = (r.lease_end - today).days
            prop = r.property
            result.append(ExpiringRenterRead(
                renter_id=r.id,
                first_name=r.first_name,
                last_name=r.last_name,
                property_id=r.property_id,
                property_address=prop.address if prop else None,
                property_city=prop.city if prop else None,
                property_owner=prop.property_owner if prop else None,
                lease_end_date=r.lease_end,
                days_until_expiry=days_left,
            ))
        return result

    def delete_renter(self, renter_id: int, owner_id: str) -> bool:
        renter = self.renter_repository.get_by_id(renter_id)
        if renter is None:
            return False
        self._check_renter_access(renter, owner_id)
        from app.services.firebase_storage import delete_file_urls
        delete_file_urls([renter.full_contract_url, renter.id_image_url])
        self.renter_repository.delete_obj(renter)
        return True

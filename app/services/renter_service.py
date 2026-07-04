import json
from datetime import date

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException

from app.config import settings
from app.models.renter import Renter
from app.repositories.cpi_index_repository import CpiIndexRepository
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.schemas.renter import ExpiringRenterRead, OverdueRenterRead, RenterCreate, RenterUpdate
from app.services.cpi_indexing_service import materialize_cpi_amounts


def _compute_lease_end(lease_start: date, lease_years_count: int) -> date:
    return lease_start + relativedelta(years=lease_years_count)


def _encode_lease_years(lease_years) -> str:
    return json.dumps([ly.model_dump() for ly in lease_years])


def _payment_interval_months(number_of_payments: int | None) -> int:
    """Months between rent payments, derived from the form's payments-per-year
    field (12=monthly, 4=quarterly, 1=yearly). Null/invalid falls back to monthly."""
    if not number_of_payments or number_of_payments <= 0:
        return 1
    return max(1, round(12 / number_of_payments))


def _is_payment_due_month(lease_start: date | None, interval_months: int, today: date) -> bool:
    """True when today's month is a payment month for a cycle anchored on
    lease_start and repeating every interval_months. Monthly (interval 1) and a
    missing lease_start are always due (preserves current behavior)."""
    if lease_start is None or interval_months <= 1:
        return True
    months_elapsed = (today.year - lease_start.year) * 12 + (today.month - lease_start.month)
    return months_elapsed >= 0 and months_elapsed % interval_months == 0


class RenterService:
    def __init__(
        self,
        renter_repository: RenterRepository,
        property_repository: PropertyRepository,
        cpi_index_repository: CpiIndexRepository | None = None,
    ):
        self.renter_repository = renter_repository
        self.property_repository = property_repository
        self.cpi_index_repository = cpi_index_repository

    def _resolve_cpi_lease_years(
        self,
        mode: str | None,
        lease_years: list[dict],
        lease_start: date | None,
        base_rent: float | None,
        existing_base_index: float | None,
        recompute_base_index: bool,
    ) -> tuple[list[dict], float | None]:
        """For a CPI-linked lease, overwrite each year's amount from the index
        linkage and return the (possibly re-frozen) base index. For any other mode
        the amounts pass through unchanged and the base index is cleared."""
        if mode != "cpi":
            return lease_years, None
        if not lease_start or not lease_years or self.cpi_index_repository is None:
            # Can't compute yet (no start date, or the cache isn't wired) — keep the
            # incoming projection; the monthly job fills real amounts in later.
            return lease_years, existing_base_index
        index_id = settings.CPI_INDEX_ID
        base = base_rent if base_rent else lease_years[0]["amount"]
        if recompute_base_index:
            base_index = self.cpi_index_repository.latest_on_or_before(index_id, lease_start)
        else:
            base_index = existing_base_index
        lookup = lambda d: self.cpi_index_repository.latest_on_or_before(index_id, d)  # noqa: E731
        materialized = materialize_cpi_amounts(lease_years, lease_start, base, base_index, lookup)
        return materialized, base_index

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
        mode = data.rent_escalation_mode.value if data.rent_escalation_mode else None
        lease_years_payload = [ly.model_dump() for ly in data.lease_years]
        lease_years_payload, cpi_base_index = self._resolve_cpi_lease_years(
            mode,
            lease_years_payload,
            data.lease_start,
            data.base_rent,
            existing_base_index=None,
            recompute_base_index=True,
        )
        renter = Renter(
            owner_id=owner_id,
            property_id=data.property_id,
            first_name=data.first_name,
            last_name=data.last_name,
            phone=data.phone,
            email=data.email,
            lease_years=json.dumps(lease_years_payload),
            lease_start=data.lease_start,
            lease_end=lease_end,
            contract_term_years=data.contract_term_years,
            option_years=data.option_years,
            base_rent=data.base_rent,
            rent_escalation_mode=mode,
            rent_escalation_value=data.rent_escalation_value,
            cpi_base_index=cpi_base_index,
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
        if "extra_contacts" in update_dict and update_dict["extra_contacts"] is not None:
            update_dict["extra_contacts"] = [c.model_dump() for c in data.extra_contacts]

        # Effective escalation mode after this update (normalized to a plain string).
        if "rent_escalation_mode" in update_dict:
            raw = update_dict["rent_escalation_mode"]
            mode = raw.value if hasattr(raw, "value") else raw
            update_dict["rent_escalation_mode"] = mode
        else:
            mode = renter.rent_escalation_mode

        lease_start = update_dict.get("lease_start", renter.lease_start)

        if mode == "cpi":
            # Server owns CPI amounts: re-materialize lease_years from the linkage,
            # using the incoming years if provided, else the stored ones for structure.
            if "lease_years" in update_dict:
                lease_years_dicts = [ly.model_dump() for ly in data.lease_years]
            else:
                lease_years_dicts = json.loads(renter.lease_years) if renter.lease_years else []
            recompute_base_index = (
                "lease_start" in update_dict
                or "base_rent" in update_dict
                or renter.cpi_base_index is None
            )
            base_rent = update_dict.get("base_rent", renter.base_rent)
            lease_years_dicts, cpi_base_index = self._resolve_cpi_lease_years(
                "cpi",
                lease_years_dicts,
                lease_start,
                base_rent,
                existing_base_index=renter.cpi_base_index,
                recompute_base_index=recompute_base_index,
            )
            update_dict["lease_years"] = json.dumps(lease_years_dicts)
            update_dict["cpi_base_index"] = cpi_base_index
        else:
            if "lease_years" in update_dict:
                update_dict["lease_years"] = _encode_lease_years(data.lease_years)
            # Switching to (or staying on) a non-CPI mode clears the frozen base index.
            if "rent_escalation_mode" in update_dict:
                update_dict["cpi_base_index"] = None

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
            interval = _payment_interval_months(r.number_of_payments)
            # Skip months where rent isn't due for this renter's cadence (e.g. the
            # off-months of a quarterly cycle, anchored on lease_start).
            if not _is_payment_due_month(r.lease_start, interval, today):
                continue

            last_day = calendar.monthrange(today.year, today.month)[1]
            pay_day = min(r.payment_day_of_month or 1, last_day)
            expected = date(today.year, today.month, pay_day)
            days_overdue = (today - expected).days

            lease_data = json.loads(r.lease_years) if isinstance(r.lease_years, str) else (r.lease_years or [])
            base_amount = lease_data[0]["amount"] if lease_data else 0.0
            # The amount owed this period spans `interval` months (e.g. 3x for quarterly).
            monthly_amount = base_amount * interval

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

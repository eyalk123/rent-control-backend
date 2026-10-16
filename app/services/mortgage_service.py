"""Mortgages on a property: the monthly payment, its history, and the day the bank collects.

Capability-gated (``capabilities.mortgage``, Israel only for now) on the **property's**
country, like every country rule: a mortgage attaches to a building. Off means off end to
end — every route here refuses, not just the screens that would offer it.
"""
from fastapi import HTTPException

from app.models.property_mortgage import MortgagePayment, PropertyMortgage
from app.repositories.owner_repository import owner_today
from app.repositories.property_mortgage_repository import PropertyMortgageRepository
from app.repositories.property_repository import PropertyRepository
from app.schemas.property_mortgage import MortgageCreate, MortgagePaymentCreate, MortgageUpdate
from app.services import country_service
from app.services.entitlement_gate import EntitlementGate


class MortgageService:
    def __init__(
        self,
        property_repository: PropertyRepository,
        entitlement_gate: EntitlementGate | None = None,
    ):
        self.property_repository = property_repository
        self.entitlement_gate = entitlement_gate
        self.repository = PropertyMortgageRepository(property_repository.session)

    def _property(self, property_id: int, owner_id: str):
        """The owner's property, or None. Refuses a locked property (402) and a country
        without the capability (422) — in that order, after the ownership lookup, so another
        owner's id still answers 404."""
        property = self.property_repository.get_by_id(property_id, owner_id)
        if property is None:
            return None
        if self.entitlement_gate is not None:
            self.entitlement_gate.require_property_unlocked(owner_id, property_id)
        if not country_service.capabilities_for(property.country).mortgage:
            config = country_service.config_for(property.country)
            raise HTTPException(
                status_code=422, detail=f"Mortgages are not available in {config.name} yet."
            )
        return property

    def _with_current(self, mortgages: list[PropertyMortgage], owner_id: str):
        """Set `current_payment` on each — judged on the landlord's own calendar day."""
        today = owner_today(self.property_repository.session, owner_id)
        for mortgage in mortgages:
            mortgage.current_payment = mortgage.payment_on(today)
        return mortgages

    def _mortgage(self, property_id: int, mortgage_id: int, owner_id: str):
        if self._property(property_id, owner_id) is None:
            return None
        return self.repository.get_by_id(mortgage_id, property_id)

    def list(self, property_id: int, owner_id: str) -> list[PropertyMortgage] | None:
        if self._property(property_id, owner_id) is None:
            return None
        return self._with_current(self.repository.get_by_property(property_id), owner_id)

    def create(self, property_id: int, data: MortgageCreate, owner_id: str) -> PropertyMortgage | None:
        if self._property(property_id, owner_id) is None:
            return None
        mortgage = PropertyMortgage(
            property_id=property_id, charge_day=data.charge_day, end_date=data.end_date
        )
        mortgage.payments.append(
            MortgagePayment(
                amount=data.monthly_payment,
                effective_from=data.payment_from
                or owner_today(self.property_repository.session, owner_id),
            )
        )
        return self._with_current([self.repository.save(mortgage)], owner_id)[0]

    def update(
        self, property_id: int, mortgage_id: int, data: MortgageUpdate, owner_id: str
    ) -> PropertyMortgage | None:
        mortgage = self._mortgage(property_id, mortgage_id, owner_id)
        if mortgage is None:
            return None
        fields = data.model_dump(exclude_unset=True)
        if fields.get("charge_day") is not None:
            mortgage.charge_day = fields["charge_day"]
        if "end_date" in fields:
            mortgage.end_date = fields["end_date"]
        return self._with_current([self.repository.save(mortgage)], owner_id)[0]

    def delete(self, property_id: int, mortgage_id: int, owner_id: str) -> bool:
        mortgage = self._mortgage(property_id, mortgage_id, owner_id)
        if mortgage is None:
            return False
        self.repository.delete(mortgage)
        return True

    def add_payment(
        self, property_id: int, mortgage_id: int, data: MortgagePaymentCreate, owner_id: str
    ) -> PropertyMortgage | None:
        """A new amount from a date on. Returns the whole mortgage, since the current payment
        may or may not be the new entry (a backdated one is not)."""
        mortgage = self._mortgage(property_id, mortgage_id, owner_id)
        if mortgage is None:
            return None
        mortgage.payments.append(
            MortgagePayment(amount=data.amount, effective_from=data.effective_from)
        )
        return self._with_current([self.repository.save(mortgage)], owner_id)[0]

    def delete_payment(
        self, property_id: int, mortgage_id: int, payment_id: int, owner_id: str
    ) -> PropertyMortgage | None:
        mortgage = self._mortgage(property_id, mortgage_id, owner_id)
        if mortgage is None:
            return None
        payment = self.repository.get_payment(payment_id, mortgage_id)
        if payment is None:
            return None
        # A mortgage with no payment is a mortgage of nothing. Removing it is what Delete is for.
        if len(mortgage.payments) <= 1:
            raise HTTPException(
                status_code=400, detail="A mortgage needs at least one payment amount"
            )
        mortgage.payments.remove(payment)
        return self._with_current([self.repository.save(mortgage)], owner_id)[0]

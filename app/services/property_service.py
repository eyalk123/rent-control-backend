import json
from datetime import date

from app.models.property import Property, PropertyTypeEnum
from app.repositories.activity_log_repository import ActivityLogRepository
from app.repositories.owner_repository import OwnerRepository
from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.schemas.property import PropertyCreate, PropertyRead, PropertyUpdate
from app.schemas.renter import PropertyRenterSummary
from app.services import country_service
from app.services.entitlement_gate import EntitlementGate
from app.services.activity_diff import changed_fields
from app.services.firebase_storage import release_file_urls

# Columns holding a Storage download URL.
_FILE_FIELDS = ("image_url", "basic_contract_url", "land_registry_url")


class PropertyService:
    def __init__(
        self,
        property_repository: PropertyRepository,
        renter_repository: RenterRepository,
        activity_log_repository: ActivityLogRepository | None = None,
        owner_repository: OwnerRepository | None = None,
        entitlement_gate: "EntitlementGate | None" = None,
    ):
        self.property_repository = property_repository
        self.renter_repository = renter_repository
        self.activity_log_repository = activity_log_repository
        # Optional for the same reason as owner_repository below: call sites that predate
        # subscriptions keep working, and a missing gate means "no plan limits", which is
        # exactly the behaviour every account had before billing existed.
        self.entitlement_gate = entitlement_gate
        # Optional so existing call sites (the test suite included) keep working; without
        # it a new property simply inherits no country, which `config_for` resolves to
        # Israel — today's behaviour exactly.
        self.owner_repository = owner_repository

    def _mark_locked(self, owner_id: str, properties: list):
        """Tag each property with whether the plan still allows writing to it.

        Resolved once per request and applied to every row, rather than asked per
        property — the gate runs two queries and doing that per item would turn a list of
        twenty into forty.

        `locked` is set on the ORM instance but is not a mapped column, so it travels to
        the response schema and is never written back to the database.
        """
        if self.entitlement_gate is None:
            return properties
        locked_ids = set(self.entitlement_gate.state_for(owner_id).locked_property_ids)
        for property in properties:
            property.locked = property.id in locked_ids
        return properties

    @staticmethod
    def _stub(property) -> PropertyRead:
        """What the list may say about a locked property: enough to recognise it, nothing more.

        The list is the one place a locked property still appears, so the owner can see what
        is locked and delete one to get back under the limit. Built from an allowlist
        rather than by blanking fields on the full row, so a column added later stays out
        of the stub until someone decides it belongs there.
        """
        return PropertyRead(
            id=property.id,
            owner_id=property.owner_id,
            address=property.address,
            city=property.city,
            type=property.type.value if hasattr(property.type, "value") else property.type,
            floor=property.floor,
            apartment=property.apartment,
            locked=True,
        )

    def list_properties(self, owner_id: str, include_locked: bool = True):
        """Every property, with locked ones reduced to a stub.

        ``include_locked=False`` drops them instead — for callers such as the assistant
        that have no use for a stub and would otherwise have to tell one apart from a row.
        """
        properties = self._mark_locked(
            owner_id, self.property_repository.get_all_by_owner(owner_id)
        )
        if self.entitlement_gate is None:
            return properties
        hidden = self.entitlement_gate.hidden_property_ids(owner_id)
        if not hidden:
            return properties
        if not include_locked:
            return [p for p in properties if p.id not in hidden]
        return [self._stub(p) if p.id in hidden else p for p in properties]

    def get_property(self, property_id: int, owner_id: str):
        property = self.property_repository.get_by_id(property_id, owner_id)
        if property is None:
            return None
        # After the ownership lookup, so another owner's id still answers 404 rather than
        # confirming it exists through a 402.
        if self.entitlement_gate is not None:
            self.entitlement_gate.require_property_unlocked(owner_id, property_id)
        return self._mark_locked(owner_id, [property])[0]

    def _owner_country_and_currency(self, owner_id: str) -> tuple[str | None, str | None]:
        """The account's country and chosen currency, or None each — never raises.

        The owners row is written best-effort (see ``get_current_owner``), so it can be
        missing for a perfectly valid request. A property with no country resolves to
        Israel in ``country_service.config_for``, which is what every row did before this
        column existed, so a miss degrades to today's behaviour rather than to an error.

        The currency is ``None`` for every account that never picked one, which then falls
        through to the country's own — the same answer this returned before the picker.
        """
        if self.owner_repository is None:
            return None, None
        owner = self.owner_repository.get(owner_id)
        if owner is None:
            return None, None
        return owner.country, owner.currency

    def create_property(self, data: PropertyCreate, owner_id: str):
        # Before any work: a plan that cannot hold another property refuses here with 402
        # and a body naming the plan that can. No-op while ENTITLEMENT_ENFORCED is off.
        if self.entitlement_gate is not None:
            self.entitlement_gate.require_can_add_property(owner_id)
        property_type = PropertyTypeEnum(data.type.value)
        country, owner_currency = self._owner_country_and_currency(owner_id)
        parking_numbers_str = (
            json.dumps(data.parking_numbers) if data.parking_numbers is not None else None
        )
        property = Property(
            owner_id=owner_id,
            address=data.address,
            city=data.city,
            zip_code=data.zip_code,
            type=property_type,
            sq_ft=data.sq_ft,
            purchase_price=data.purchase_price,
            image_url=data.image_url,
            number_of_rooms=data.number_of_rooms,
            parking_numbers=parking_numbers_str,
            electricity_meter_number=data.electricity_meter_number,
            electricity_account_number=data.electricity_account_number,
            water_meter_number=data.water_meter_number,
            water_account_number=data.water_account_number,
            property_tax=data.property_tax,
            house_committee=data.house_committee,
            property_owner=data.property_owner,
            basic_contract_url=data.basic_contract_url,
            land_registry_url=data.land_registry_url,
            floor=data.floor,
            apartment=data.apartment,
            block=data.block,
            plot=data.plot,
            # Copied from the account, silently — the form has no country picker and the
            # user is asked nothing extra. It is copied rather than read through a join
            # because it must not move if the account's country is ever corrected: a lease
            # already priced under one country's rules cannot be re-based by an edit
            # somewhere else.
            country=country,
            # Frozen at creation from the country, not read through a join, for the same
            # reason `country` is: a transaction already recorded in one currency cannot be
            # re-denominated by an edit somewhere else. `transaction_service` snapshots this
            # onto every row it writes.
            currency_code=country_service.effective_currency(country, owner_currency).code,
        )
        created = self.property_repository.create(property)
        return self.property_repository.get_by_id(created.id, owner_id)

    def update_property(self, property_id: int, data: PropertyUpdate, owner_id: str):
        property = self.property_repository.get_by_id(property_id, owner_id)
        if property is None:
            return None
        # A property over the plan's ceiling is closed to edits as well as reads. Checked
        # after the ownership lookup so a property belonging to someone else still answers
        # 404 rather than leaking its existence through a 402.
        if self.entitlement_gate is not None:
            self.entitlement_gate.require_property_unlocked(owner_id, property_id)
        update_dict = data.model_dump(exclude_unset=True)
        if "type" in update_dict and update_dict["type"] is not None:
            update_dict["type"] = PropertyTypeEnum(update_dict["type"].value)
        if "parking_numbers" in update_dict:
            update_dict["parking_numbers"] = (
                json.dumps(update_dict["parking_numbers"])
                if update_dict["parking_numbers"] is not None
                else None
            )
        # After the normalisation above, and before the update is applied: `type` has to be
        # compared enum-to-enum and `parking_numbers` string-to-string, or an untouched
        # field reads as an edit on every save.
        if self.activity_log_repository is not None:
            changed = changed_fields(property, update_dict)
            if changed:
                self.activity_log_repository.record_action(
                    owner_id=owner_id,
                    action="update",
                    entity_type="property",
                    entity_id=property.id,
                    label=", ".join(p for p in (property.address, property.city) if p),
                    details={"fields": changed},
                )
        replaced = [
            getattr(property, f)
            for f in _FILE_FIELDS
            if f in update_dict and update_dict[f] != getattr(property, f)
        ]
        self.property_repository.update(property, update_dict)
        release_file_urls(self.property_repository.session, owner_id, replaced)
        return self.property_repository.get_by_id(property_id, owner_id)

    def delete_property(self, property_id: int, owner_id: str) -> bool:
        property = self.property_repository.get_by_id(property_id, owner_id)
        if property is None:
            return False
        # Read before the delete: `property_files` rows go with it (ON DELETE CASCADE).
        urls = [getattr(property, f) for f in _FILE_FIELDS] + [f.url for f in property.files]

        if self.activity_log_repository is not None:
            # Recorded before the delete so the address is still readable.
            self.activity_log_repository.record_delete(
                owner_id=owner_id,
                entity_type="property",
                entity_id=property.id,
                label=", ".join(p for p in (property.address, property.city) if p),
                details={"property_owner": property.property_owner},
            )

        self.property_repository.delete_obj(property)
        release_file_urls(self.property_repository.session, owner_id, urls)
        return True

    def get_property_renters(self, property_id: int, owner_id: str, include_ended: bool = False):
        """Renters linked to the property, for e.g. the add-revenue form.

        Active leases only by default. ``include_ended`` is what the transaction form
        asks for: a payment can arrive after a tenancy finishes (the last month's rent
        routinely lands late), and editing an old transaction has to be able to show the
        renter it was already attached to — an option that silently vanished from the
        dropdown would detach the transaction on the next save.
        """
        property = self.property_repository.get_by_id(property_id, owner_id)
        if property is None:
            return None
        if self.entitlement_gate is not None:
            self.entitlement_gate.require_property_unlocked(owner_id, property_id)
        renters = self.renter_repository.get_by_property_id(
            property_id=property_id,
            owner_id=owner_id,
            active_only=not include_ended,
        )
        today = date.today()
        summaries = []
        for r in renters:
            lease_years_data = r.lease_years
            if isinstance(lease_years_data, str):
                lease_years_data = json.loads(lease_years_data)
            monthly_rent = 0.0
            if lease_years_data:
                # lease_years[i]["amount"] is stored as the MONTHLY rent everywhere
                # else (overdue engine, extraction prompt, factories). Do not divide.
                monthly_rent = lease_years_data[0]["amount"]
            # Same rule the repository's active window uses: an early termination beats
            # the signed end date.
            effective_end = min(d for d in (r.terminated_on, r.lease_end) if d) if (
                r.terminated_on or r.lease_end
            ) else None
            summaries.append(
                PropertyRenterSummary(
                    id=r.id,
                    first_name=r.first_name,
                    last_name=r.last_name,
                    monthly_rent=monthly_rent,
                    is_ended=bool(effective_end and effective_end < today),
                )
            )
        return summaries

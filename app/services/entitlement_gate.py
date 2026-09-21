"""Enforcement: turns the plan rules into allow/deny decisions for a given owner.

Split from ``entitlement_service`` on purpose. That module is the *rules* — pure
functions over plan names and counts, no database, trivially testable. This one is the
*enforcement* — it reads the account's real state and raises the HTTP errors. Keeping
them apart is what lets the boundaries be re-tuned without touching anything that talks
to a session, and lets the gate be unit-tested without inventing subscriptions.

Two sources of entitlement are combined here, and neither may cancel the other:

* the **subscription** someone pays for, resolved through ``effective_plan``;
* a permanent **grant** on ``owners.granted_plan`` — how every landlord who was already
  using the product before billing existed keeps top-tier access for good.

``better_plan`` takes whichever permits more. A grandfathered landlord who later buys a
small plan must not lose capacity for paying, and a lapsed subscription must not erase a
grant that was never conditional on payment in the first place.
"""
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.property import Property
from app.services import entitlement_service as ent
from app.services.entitlement_service import PlanLimits

#: Returned instead of 403 so clients can tell "you must pay" apart from "you may not
#: touch this". 402 Payment Required is the one status that means exactly this, and the
#: structured body is what lets a client render a real paywall naming the right plan
#: rather than a generic error toast.
PAYMENT_REQUIRED = status.HTTP_402_PAYMENT_REQUIRED


@dataclass(frozen=True)
class EntitlementState:
    """Everything the product needs to know about one account's plan, resolved once."""

    plan: PlanLimits
    property_count: int
    #: Properties the account may write to, oldest first.
    writable_property_ids: list[int]
    #: Properties over the plan's ceiling — readable and exportable, never writable.
    locked_property_ids: list[int]
    #: Whether a paid grant or grandfather grant is what is providing the plan.
    granted: bool
    #: False while ENTITLEMENT_ENFORCED is off: the answer is computed and reported, but
    #: nothing is refused.
    enforced: bool

    @property
    def over_limit(self) -> bool:
        return bool(self.locked_property_ids)

    def as_error_body(self) -> dict:
        """The 402 payload. Named fields, not a sentence: the client renders the paywall."""
        required = ent.required_plan_for(self.property_count)
        return {
            "error": "plan_limit_reached",
            "current_plan": self.plan.plan,
            "current_count": self.property_count,
            "limit": self.plan.max_properties,
            "required_plan": required.plan,
            "locked_property_ids": self.locked_property_ids,
        }


class EntitlementGate:
    """Resolves and enforces one account's plan."""

    def __init__(self, session: Session):
        self.session = session

    # ── Resolution ───────────────────────────────────────────────────────────

    def state_for(self, owner_id: str) -> EntitlementState:
        """Resolve everything about this account's plan in one pass.

        Deliberately one object rather than a handful of small queries: a request that
        checks the limit, then asks which properties are locked, then asks again for the
        error body would run the same two queries three times.
        """
        # Imported here rather than at module scope: importing the repositories package
        # at import time pulls in the model layer before Base is fully populated in some
        # entry points (alembic in particular).
        from app.repositories.owner_repository import OwnerRepository
        from app.repositories.subscription_repository import SubscriptionRepository

        owner = OwnerRepository(self.session).get(owner_id)
        granted_plan = owner.granted_plan if owner is not None else None

        subscription = SubscriptionRepository(self.session).get_for_owner(owner_id)
        if subscription is None:
            paid_plan = ent.limits_for(None)
        else:
            paid_plan = ent.effective_plan(
                subscription.plan,
                subscription.status,
                subscription.current_period_end,
                subscription.grace_until,
            )

        plan = ent.better_plan(granted_plan, paid_plan.plan)

        # Oldest first, id as the tiebreak so the order is total even when two properties
        # share a created_at (a seeded account, or an import). An unstable sort here would
        # mean the locked set could differ between two requests, which is precisely the
        # failure this ordering exists to prevent.
        ordered_ids = list(
            self.session.scalars(
                select(Property.id)
                .where(Property.owner_id == owner_id)
                .order_by(Property.created_at.asc(), Property.id.asc())
            ).all()
        )

        writable, locked = ent.split_by_allowance(ordered_ids, plan)

        return EntitlementState(
            plan=plan,
            property_count=len(ordered_ids),
            writable_property_ids=writable,
            locked_property_ids=locked,
            granted=not plan.is_free,
            enforced=settings.ENTITLEMENT_ENFORCED,
        )

    # ── Enforcement ──────────────────────────────────────────────────────────

    def require_can_add_property(self, owner_id: str) -> EntitlementState:
        """Refuse a new property that the plan would not cover.

        Checked against the count *after* the addition: an account at its ceiling is
        compliant, and only the next one crosses the line.
        """
        state = self.state_for(owner_id)
        if not state.enforced:
            return state
        if ent.allows_property_count(state.plan.plan, state.property_count + 1):
            return state
        body = state.as_error_body()
        # required_plan is computed from the *current* count everywhere else; here the
        # answer the landlord needs is the plan that covers the one they are adding.
        body["required_plan"] = ent.required_plan_for(state.property_count + 1).plan
        body["current_count"] = state.property_count
        raise HTTPException(status_code=PAYMENT_REQUIRED, detail=body)

    def require_property_writable(self, owner_id: str, property_id: int | None) -> None:
        """Refuse a write to a property over the plan's ceiling.

        ``property_id`` of ``None`` passes: a transaction or renter that belongs to no
        property is not gated by any property's lock.

        This covers the property itself *and* its dependent records — a locked property
        that still accepted rent entries and lease edits would not be locked in any sense
        a landlord would recognise, and the half-locked version is harder to explain than
        the whole one. Reads, reports and exports are never gated: nothing is hidden and
        nothing is deleted, which is what the published refund policy promises.
        """
        if property_id is None:
            return
        state = self.state_for(owner_id)
        if not state.enforced:
            return
        if property_id in state.locked_property_ids:
            body = state.as_error_body()
            body["error"] = "property_locked"
            body["property_id"] = property_id
            raise HTTPException(status_code=PAYMENT_REQUIRED, detail=body)

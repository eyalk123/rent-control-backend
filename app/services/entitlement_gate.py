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
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.property import Property
from app.services import entitlement_service as ent
from app.services.entitlement_service import PlanLimits

def _month_start() -> datetime:
    """00:00 UTC on the first of the current month — when the scan allowance resets."""
    now = datetime.now(tz=timezone.utc)
    return datetime(now.year, now.month, 1)


def _next_month_start() -> datetime:
    """00:00 UTC on the first of next month, so a client can say when the quota returns."""
    start = _month_start()
    return (
        datetime(start.year + 1, 1, 1)
        if start.month == 12
        else datetime(start.year, start.month + 1, 1)
    )


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
    #: Properties over the plan's ceiling — closed to everything but the properties list
    #: (as a stub), deletion and the account export.
    locked_property_ids: list[int]
    #: Whether a paid grant or grandfather grant is what is providing the plan.
    granted: bool
    #: False while ENTITLEMENT_ENFORCED is off: the answer is computed and reported, but
    #: nothing is refused.
    enforced: bool
    #: Whether the client should show the one-time "why are some properties locked"
    #: explanation. See `EntitlementGate.acknowledge_lock_notice`.
    show_lock_notice: bool = False

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

        # Show the explanation when properties are actually locked and this landlord has
        # not been told about *this* plan yet. Comparing the plan rather than a boolean is
        # what makes a second downgrade — to a different band, with a different number of
        # locked properties — explain itself again.
        acknowledged = owner.lock_notice_ack_plan if owner is not None else None
        show_notice = bool(locked) and acknowledged != plan.plan

        return EntitlementState(
            plan=plan,
            property_count=len(ordered_ids),
            writable_property_ids=writable,
            locked_property_ids=locked,
            granted=not plan.is_free,
            enforced=settings.ENTITLEMENT_ENFORCED,
            show_lock_notice=show_notice,
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

    def hidden_property_ids(self, owner_id: str) -> frozenset[int]:
        """Properties every read must leave out: the locked ones, while enforcement is on.

        The single place read paths ask, so ``ENTITLEMENT_ENFORCED`` is honoured once rather
        than at every list, summary, report and job that filters. Empty while enforcement
        is off, which leaves every read exactly as it was.
        """
        state = self.state_for(owner_id)
        if not state.enforced:
            return frozenset()
        return frozenset(state.locked_property_ids)

    def require_property_unlocked(self, owner_id: str, property_id: int | None) -> None:
        """Refuse any access — read or write — to a property over the plan's ceiling.

        ``property_id`` of ``None`` passes: a transaction or renter that belongs to no
        property is not gated by any property's lock.

        This covers the property itself *and* its dependent records: its renters,
        transactions and documents. A locked property is not a read-only one — a downgraded
        account that could still browse every property it no longer pays for would have
        lost almost nothing. Three things stay open on purpose, and none of them come
        through here: the properties list shows it as a stub, deleting it is allowed (that
        is how an account gets back under its limit), and the account export includes it
        (it is the landlord's data, and paying must not be the only way to get it back).
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

    def acknowledge_lock_notice(self, owner_id: str) -> None:
        """Record that the over-limit explanation has been shown for the current plan.

        Stores the plan rather than a flag, so the notice returns if the landlord later
        lands in a *different* restricted plan — a second downgrade locks a different set
        of properties and deserves saying so again.

        Idempotent, and harmless to call when nothing is locked: acknowledging a notice
        that was not shown simply records the current plan.
        """
        from app.repositories.owner_repository import OwnerRepository

        state = self.state_for(owner_id)
        OwnerRepository(self.session).set_lock_notice_ack(owner_id, state.plan.plan)

    # ── Feature gates ────────────────────────────────────────────────────────

    def require_lease_scan(self, owner_id: str) -> None:
        """Refuse a lease scan once the plan's monthly allowance is spent.

        Checked *before* the file reaches Anthropic, not after: the quota exists to bound
        what a free account can spend, and a check that runs after the model call has
        already spent it.

        The month is a calendar month in UTC, because "3 scans a month" is what the
        pricing page says — a rolling 30-day window is a different promise, and a
        local-calendar boundary would move the reset by the host's offset.

        Only successful scans count. A scan that failed on an unreadable file cost the
        landlord nothing and burning a third of their monthly allowance on it would be
        indefensible.
        """
        from app.repositories.subscription_repository import SubscriptionRepository

        state = self.state_for(owner_id)
        if not state.enforced:
            return
        allowance = state.plan.monthly_lease_scans
        if allowance is None:
            return

        used = SubscriptionRepository(self.session).count_lease_scans_since(
            owner_id, _month_start()
        )
        if used < allowance:
            return

        raise HTTPException(
            status_code=PAYMENT_REQUIRED,
            detail={
                "error": "scan_limit_reached",
                "current_plan": state.plan.plan,
                "limit": allowance,
                "used": used,
                "required_plan": ent.PLANS[1].plan,
                "resets_at": _next_month_start().isoformat(),
            },
        )

    def require_agent(self, owner_id: str) -> None:
        """Refuse the chat assistant on a plan that does not include it."""
        state = self.state_for(owner_id)
        if not state.enforced:
            return
        if state.plan.agent:
            return
        raise HTTPException(
            status_code=PAYMENT_REQUIRED,
            detail={
                "error": "agent_not_included",
                "current_plan": state.plan.plan,
                "required_plan": ent.PLANS[1].plan,
            },
        )

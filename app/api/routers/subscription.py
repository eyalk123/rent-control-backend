"""The account's plan, as one endpoint both clients read.

`GET /subscription` is the single place a client learns what its plan permits. Deriving
that on the client — a plan name plus a property count, compared against band boundaries
written into the app — would put the boundaries in three codebases and guarantee they
disagree the first time a price moves, in the two of them that cannot be hotfixed because
they are waiting on store review.
"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_entitlement_gate
from app.database import get_db
from app.repositories.subscription_repository import SubscriptionRepository
from app.schemas.subscription import SubscriptionRead
from app.services import entitlement_service as ent
from app.services.entitlement_gate import EntitlementGate

router = APIRouter()


def _month_start() -> datetime:
    """00:00 UTC on the first of the current month.

    The scan quota resets on a calendar boundary rather than a rolling 30-day window,
    because "3 scans a month" is what the pricing page says and a rolling window is not
    that. UTC, to match every timestamp column — a local-calendar boundary would move the
    reset by the host's offset.
    """
    now = datetime.now(tz=timezone.utc)
    return datetime(now.year, now.month, 1)


@router.get("", response_model=SubscriptionRead)
def get_subscription(
    current_user: Annotated[dict, Depends(get_current_user)],
    gate: Annotated[EntitlementGate, Depends(get_entitlement_gate)],
    db: Annotated[Session, Depends(get_db)],
):
    """What this account's plan is and what it permits."""
    owner_id = current_user["user_id"]
    state = gate.state_for(owner_id)

    repository = SubscriptionRepository(db)
    subscription = repository.get_for_owner(owner_id)

    return SubscriptionRead(
        plan=state.plan.plan,
        limit=state.plan.max_properties,
        property_count=state.property_count,
        required_plan=ent.required_plan_for(state.property_count).plan,
        locked_property_ids=state.locked_property_ids,
        show_lock_notice=state.show_lock_notice,
        enforced=state.enforced,
        source=subscription.source if subscription else None,
        status=subscription.status if subscription else None,
        period=subscription.period if subscription else None,
        current_period_end=subscription.current_period_end if subscription else None,
        price_amount=float(subscription.price_amount)
        if subscription and subscription.price_amount is not None
        else None,
        price_currency=subscription.price_currency if subscription else None,
        monthly_lease_scans=state.plan.monthly_lease_scans,
        lease_scans_used=repository.count_lease_scans_since(owner_id, _month_start()),
        agent=state.plan.agent,
    )


@router.post("/lock-notice/ack", status_code=204)
def acknowledge_lock_notice(
    current_user: Annotated[dict, Depends(get_current_user)],
    gate: Annotated[EntitlementGate, Depends(get_entitlement_gate)],
):
    """Record that the over-limit explanation has been shown.

    Called by a client once it has displayed the notice, not before. It records the plan
    rather than a flag, so a landlord who later lands in a *different* restricted plan is
    told again — a second downgrade locks a different set of properties.
    """
    gate.acknowledge_lock_notice(current_user["user_id"])

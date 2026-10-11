"""The account's plan, as one endpoint both clients read.

`GET /subscription` is the single place a client learns what its plan permits. Deriving
that on the client — a plan name plus a property count, compared against band boundaries
written into the app — would put the boundaries in three codebases and guarantee they
disagree the first time a price moves, in the two of them that cannot be hotfixed because
they are waiting on store review.
"""
from datetime import datetime, timezone
from typing import Annotated

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_entitlement_gate
from app.clock import utc_now_naive
from app.database import get_db
from app.repositories.subscription_repository import SubscriptionRepository
from app.schemas.subscription import ChangePlanRequest, ManageLinks, SubscriptionRead
from app.services import entitlement_service as ent
from app.services import paddle_api
from app.services.billing_catalog import paddle_price_for
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
        lease_scans_used=repository.count_scans_since(owner_id, _month_start(), "lease"),
        monthly_receipt_scans=state.plan.monthly_receipt_scans,
        receipt_scans_used=repository.count_scans_since(owner_id, _month_start(), "receipt"),
        agent=state.plan.agent,
    )


#: Paid plans, smallest first — what "bigger" means for an upgrade.
_PLAN_ORDER = [ent.PLAN_TIER_3_8, ent.PLAN_TIER_9_15, ent.PLAN_TIER_16_PLUS]


def _web_subscription(db: Session, owner_id: str):
    """This owner's Paddle subscription row, or 409 if their plan is not billed on the web.

    Apple and Google subscriptions can only be managed in their stores; the clients send
    those there and never call the endpoints below.
    """
    subscription = SubscriptionRepository(db).get_for_owner(owner_id)
    if (
        subscription is None
        or subscription.source != "paddle"
        or not paddle_api.is_subscription_id(subscription.external_id)
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="not_web_subscription")
    return subscription


def _paddle_failure(exc: Exception) -> HTTPException:
    # Sentry ignores HTTP statuses (app/monitoring.py); capture the cause first.
    sentry_sdk.capture_exception(exc)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, detail="billing_provider_unavailable")


@router.get("/manage-links", response_model=ManageLinks)
def get_manage_links(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Paddle's own cancel and update-payment pages for a web subscription.

    Fetched on demand because the links carry a short-lived token.
    """
    subscription = _web_subscription(db, current_user["user_id"])
    try:
        urls = paddle_api.get_subscription(subscription.external_id).get("management_urls") or {}
    except (paddle_api.NotConfigured, paddle_api.PaddleError) as exc:
        raise _paddle_failure(exc) from exc
    return ManageLinks(
        cancel_url=urls.get("cancel"),
        update_payment_method_url=urls.get("update_payment_method"),
    )


@router.post("/change-plan", response_model=SubscriptionRead)
def change_plan(
    body: ChangePlanRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
    gate: Annotated[EntitlementGate, Depends(get_entitlement_gate)],
    db: Annotated[Session, Depends(get_db)],
):
    """Move a web subscription up to a bigger plan, prorated now.

    Upgrades only. The refund policy promises that a smaller plan takes effect at the next
    renewal, and Paddle cannot schedule a price change for a future date — so a downgrade
    is a support request, not a button that would credit and switch immediately.

    The row is updated from Paddle's answer straight away rather than waiting for the
    webhook: Paddle has confirmed the change and charged for it, and RevenueCat reports a
    product change as informative only, so the webhook may not carry it until renewal.
    """
    owner_id = current_user["user_id"]
    subscription = _web_subscription(db, owner_id)
    if subscription.status not in ("active", "trialing"):
        # Paddle refuses changes to a subscription that is cancelling or past due, and
        # the landlord's next step there is to resume or pay, not to switch plan.
        raise HTTPException(status.HTTP_409_CONFLICT, detail="subscription_not_active")
    if _PLAN_ORDER.index(body.plan) <= _PLAN_ORDER.index(subscription.plan):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="not_an_upgrade")
    price_id = paddle_price_for(body.plan, body.period)
    if price_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unknown_plan")

    try:
        updated = paddle_api.change_price(subscription.external_id, price_id)
    except (paddle_api.NotConfigured, paddle_api.PaddleError) as exc:
        raise _paddle_failure(exc) from exc

    period_end = (updated.get("current_billing_period") or {}).get("ends_at")
    SubscriptionRepository(db).upsert(
        owner_id,
        plan=body.plan,
        period=body.period,
        status=subscription.status,
        source=subscription.source,
        external_id=subscription.external_id,
        current_period_end=_parse_paddle_time(period_end) or subscription.current_period_end,
        grace_until=subscription.grace_until,
        price_amount=None,
        price_currency=subscription.price_currency,
        last_event_at=utc_now_naive(),
    )
    return get_subscription(current_user, gate, db)


def _parse_paddle_time(value: str | None) -> datetime | None:
    """Paddle's RFC 3339 timestamp -> naive UTC, matching every timestamp column."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


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

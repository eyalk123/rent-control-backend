"""The account's plan, as one endpoint both clients read.

`GET /subscription` is the single place a client learns what its plan permits. Deriving
that on the client — a plan name plus a property count, compared against band boundaries
written into the app — would put the boundaries in three codebases and guarantee they
disagree the first time a price moves, in the two of them that cannot be hotfixed because
they are waiting on store review.
"""
import threading
import time
from datetime import datetime, timezone
from typing import Annotated

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_entitlement_gate
from app.clock import utc_now_naive
from app.database import get_db
from app.repositories.subscription_repository import SubscriptionRepository
from app.schemas.subscription import ChangePlanRequest, DiscountLookup, ManageLinks, SubscriptionRead
from app.services import entitlement_service as ent
from app.services import discount_service, paddle_api, revenuecat_api
from app.services.billing_catalog import paddle_price_for
from app.services.entitlement_gate import EntitlementGate
from app.services.revenuecat_service import apply_store_subscription

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


@router.post("/sync", response_model=SubscriptionRead)
def sync_subscription(
    current_user: Annotated[dict, Depends(get_current_user)],
    gate: Annotated[EntitlementGate, Depends(get_entitlement_gate)],
    db: Annotated[Session, Depends(get_db)],
):
    """Ask RevenueCat what this account holds now, and save it.

    Called by the app after a purchase or restore. The webhook stays the normal path, but
    RevenueCat sends one only when something changes: restoring a purchase the account
    already owns sends nothing, so a row the webhook never wrote would stay missing.

    Only ever grants or refreshes. Finding nothing leaves the row alone — an expiry is
    the webhook's to report, and a lookup that misses a web subscription must not end it.
    """
    owner_id = current_user["user_id"]
    try:
        held = revenuecat_api.current_subscription(owner_id)
    except revenuecat_api.NotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="sync_unavailable") from exc
    except revenuecat_api.LookupFailed as exc:
        sentry_sdk.capture_exception(exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="billing_provider_unavailable") from exc

    if held is not None:
        apply_store_subscription(SubscriptionRepository(db), owner_id, held, utc_now_naive())
    return get_subscription(current_user, gate, db)


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


# ── Discount code lookup ─────────────────────────────────────────────────────
# A fixed window per account, in memory — the same shape as the admin limiter. The lookup
# tells anyone signed in whether a code exists, so this is the brake on guessing codes by
# trying them. Per worker, so the real ceiling is this times the worker count; still a few
# dozen guesses a minute against a 32-character alphabet-and-digits space.
_DISCOUNT_LOOKUPS_PER_MINUTE = 10
_discount_hits: dict[str, list[float]] = {}
_discount_hits_lock = threading.Lock()


def _limit_discount_lookups(owner_id: str) -> None:
    now = time.monotonic()
    cutoff = now - 60.0
    with _discount_hits_lock:
        recent = [t for t in _discount_hits.get(owner_id, []) if t > cutoff]
        if len(recent) >= _DISCOUNT_LOOKUPS_PER_MINUTE:
            _discount_hits[owner_id] = recent
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too_many_lookups",
                headers={"Retry-After": "60"},
            )
        recent.append(now)
        _discount_hits[owner_id] = recent
        if len(_discount_hits) > 2048:
            for key in [k for k, v in _discount_hits.items() if not v or max(v) < cutoff]:
                _discount_hits.pop(key, None)


@router.get("/discount", response_model=DiscountLookup)
def lookup_discount(
    current_user: Annotated[dict, Depends(get_current_user)],
    # Paddle's own rule for a code. Matching ignores case, so it is passed on as typed.
    code: Annotated[str, Query(pattern=r"^[A-Za-z0-9]{1,32}$")],
):
    """Whether a Paddle discount code can be used, and what it gives.

    An unusable code is a normal answer (200, `valid: false` and a reason), not an
    error: the page shows the reason under the field. Errors are reserved for not being
    able to ask — 429 past the rate limit, 502 when Paddle cannot be reached.
    """
    _limit_discount_lookups(current_user["user_id"])
    try:
        found = paddle_api.find_discount(code)
        return discount_service.evaluate(code, found, paddle_api.price_ids_for_product)
    except (paddle_api.NotConfigured, paddle_api.PaddleError) as exc:
        raise _paddle_failure(exc) from exc


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

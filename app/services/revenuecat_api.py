"""Reading a customer's current subscription from RevenueCat's REST API.

The webhook is the source of billing state; this exists for the one event that does not
carry enough to act on. A TRANSFER names who the purchase moved from and to, but not
which product it is. When the previous owner has no row to move — the account was
deleted, or its purchase was never applied — the only place left to learn what the new
owner is paying for is RevenueCat itself.

Uses the v1 subscriber endpoint with a secret key (`REVENUECAT_API_KEY`). Without a key
the lookup reports `NotConfigured` and the caller leaves entitlement unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from app.clock import utc_now_naive
from app.config import settings
from app.services.billing_catalog import PRODUCT_PLANS

logger = logging.getLogger(__name__)

SUBSCRIBER_URL = "https://api.revenuecat.com/v1/subscribers/{app_user_id}"
_TIMEOUT_SECONDS = 5


class NotConfigured(Exception):
    """No `REVENUECAT_API_KEY`: the lookup cannot run."""


class LookupFailed(Exception):
    """RevenueCat could not be reached or answered with something unusable."""


@dataclass(frozen=True)
class StoreSubscription:
    """The one subscription a customer currently holds, already mapped to a plan."""

    plan: str
    period: str
    status: str
    store: str | None
    external_id: str | None
    current_period_end: datetime | None
    grace_until: datetime | None
    price_amount: float | None
    price_currency: str | None


def _parse_date(value) -> datetime | None:
    """RevenueCat's ISO-8601 string -> naive UTC, matching every timestamp column."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _map_product(product_id: str, entry: dict) -> tuple[str, str] | None:
    """The catalog entry for a v1 subscription key.

    Play subscriptions may be keyed by the subscription id alone with the base plan in
    `product_plan_identifier`; the catalog holds them as `subscriptionId:basePlanId`.
    """
    mapped = PRODUCT_PLANS.get(product_id)
    if mapped is None and entry.get("product_plan_identifier"):
        mapped = PRODUCT_PLANS.get(f"{product_id}:{entry['product_plan_identifier']}")
    return mapped


def _status(entry: dict) -> str:
    # Same meanings as the webhook's EVENT_STATUS: a detected unsubscribe is a
    # cancellation that still runs to the period end, not an expiry.
    if entry.get("billing_issues_detected_at"):
        return "past_due"
    if entry.get("unsubscribe_detected_at"):
        return "canceled"
    return "active"


def current_subscription(app_user_id: str) -> StoreSubscription | None:
    """The customer's unexpired, catalogued subscription, or None if they hold none.

    Raises `NotConfigured` without an API key and `LookupFailed` when RevenueCat cannot
    answer, so the caller can tell "nothing to grant" from "could not find out".
    """
    if not settings.REVENUECAT_API_KEY:
        raise NotConfigured()

    try:
        response = requests.get(
            SUBSCRIBER_URL.format(app_user_id=quote(app_user_id, safe="")),
            headers={"Authorization": f"Bearer {settings.REVENUECAT_API_KEY}"},
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        subscriptions = (response.json().get("subscriber") or {}).get("subscriptions") or {}
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise LookupFailed(str(exc)) from exc

    now = utc_now_naive()
    best: StoreSubscription | None = None
    for product_id, entry in subscriptions.items():
        if not isinstance(entry, dict):
            continue
        expires = _parse_date(entry.get("expires_date"))
        grace = _parse_date(entry.get("grace_period_expires_date"))
        if expires is not None and expires <= now and (grace is None or grace <= now):
            continue
        if entry.get("is_sandbox") and not settings.REVENUECAT_APPLY_SANDBOX:
            continue
        mapped = _map_product(product_id, entry)
        if mapped is None:
            logger.warning("RevenueCat subscription for unmapped product %s", product_id)
            continue

        price = entry.get("price") or {}
        candidate = StoreSubscription(
            plan=mapped[0],
            period=mapped[1],
            status=_status(entry),
            store=entry.get("store"),
            external_id=entry.get("original_transaction_id") or entry.get("store_transaction_id"),
            current_period_end=expires,
            grace_until=grace,
            price_amount=price.get("amount"),
            price_currency=price.get("currency"),
        )
        # More than one unexpired entry means a product change mid-period; the one
        # that runs longest is the one the store will keep renewing.
        if best is None or (candidate.current_period_end or datetime.max) > (
            best.current_period_end or datetime.max
        ):
            best = candidate
    return best

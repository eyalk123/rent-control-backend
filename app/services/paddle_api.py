"""The three things the backend asks of Paddle directly: read, cancel, change plan.

Buying goes through RevenueCat's web SDK and every state change still arrives by
RevenueCat's webhook. This exists for what RevenueCat does not do for Paddle: it cannot
cancel or change a Paddle subscription, and it hands out no manage links unless its own
Paddle key has extra permissions. Paddle's API does all three with the subscription id,
which RevenueCat reports as `original_transaction_id` (`sub_…`) and the webhook stores as
`subscriptions.external_id`.

It also reads discounts, so `/plans` can say what a code gives before checkout opens —
RevenueCat's web SDK hides Paddle's own discount box, so the page collects the code itself.

`PADDLE_API_KEY` must allow reading and writing subscriptions, and reading discounts and
prices; `PADDLE_ENV` picks the sandbox or live API.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

import requests

from app.config import settings

logger = logging.getLogger(__name__)

_LIVE_URL = "https://api.paddle.com"
_SANDBOX_URL = "https://sandbox-api.paddle.com"
_TIMEOUT_SECONDS = 10


class NotConfigured(Exception):
    """No `PADDLE_API_KEY`."""


class PaddleError(Exception):
    """Paddle could not be reached or refused the request."""


def is_subscription_id(value: str | None) -> bool:
    return bool(value) and str(value).startswith("sub_")


def _request(method: str, path: str, json: dict | None = None) -> dict | list:
    if not settings.PADDLE_API_KEY:
        raise NotConfigured()
    base = _SANDBOX_URL if settings.PADDLE_ENV.strip().lower() == "sandbox" else _LIVE_URL
    try:
        response = requests.request(
            method,
            f"{base}{path}",
            json=json,
            headers={"Authorization": f"Bearer {settings.PADDLE_API_KEY}"},
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json().get("data") or {}
    except (requests.RequestException, ValueError, AttributeError) as exc:
        # The body names what Paddle objected to; it holds ids, never card data.
        body = getattr(getattr(exc, "response", None), "text", "")
        raise PaddleError(f"{exc} {body[:300]}".strip()) from exc


def get_subscription(subscription_id: str) -> dict:
    """The subscription, including `management_urls` (cancel, update_payment_method).

    Those URLs carry a short-lived token, so they are fetched when someone asks for
    them rather than stored.
    """
    return _request("GET", f"/subscriptions/{subscription_id}")


def cancel_now(subscription_id: str) -> dict:
    """Cancel immediately. Used on account deletion, where there is no account left to
    use the rest of the period."""
    return _request(
        "POST", f"/subscriptions/{subscription_id}/cancel", {"effective_from": "immediately"}
    )


def change_price(subscription_id: str, price_id: str) -> dict:
    """Move the subscription to another price, charging or crediting the difference now.

    Immediate proration is what an upgrade needs — the landlord who has just added a
    ninth property wants it unlocked today — and applying the same to a downgrade keeps
    one rule instead of two.
    """
    return _request(
        "PATCH",
        f"/subscriptions/{subscription_id}",
        {
            "items": [{"price_id": price_id, "quantity": 1}],
            "proration_billing_mode": "prorated_immediately",
        },
    )


def find_discount(code: str) -> dict | None:
    """The active discount with this code, or None. Paddle matches codes ignoring case."""
    found = _request("GET", f"/discounts?{urlencode({'code': code, 'status': 'active'})}")
    return found[0] if isinstance(found, list) and found else None


def price_ids_for_product(product_id: str) -> list[str]:
    """Every price of a product — a discount restricted to a product covers all of them."""
    found = _request("GET", f"/prices?{urlencode({'product_id': product_id, 'per_page': 200})}")
    return [p["id"] for p in found if p.get("id")] if isinstance(found, list) else []

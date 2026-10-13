"""What a Paddle discount code gives, worked out before checkout opens.

`/plans` on the web collects the code itself — RevenueCat's SDK opens Paddle with Paddle's
own discount box hidden — so without this the landlord learns whether a code works only
inside checkout. This answers the question first: does the code exist, can it still be
used, and which of our plans does it cover.

The amounts shown on the page do not come from here. The client hands the discount id to
Paddle's price preview, which applies it in the visitor's currency exactly as checkout
will. This module supplies the terms (how much, for how long, on which plans) and the
reason when a code cannot be used.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from app.services.billing_catalog import PRODUCT_PLANS


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _covered_plans(
    restrict_to: list[str] | None, prices_of_product: Callable[[str], list[str]]
) -> list[str] | None:
    """`<plan>_<period>` keys the discount applies to; None when it applies to everything.

    `restrict_to` mixes price ids and product ids. A product id covers all of that
    product's prices, which only Paddle knows, so those are asked for — one call per
    product, and only for a restricted code.
    """
    if not restrict_to:
        return None
    price_ids: set[str] = set()
    for item in restrict_to:
        if item.startswith("pri_"):
            price_ids.add(item)
        elif item.startswith("pro_"):
            price_ids.update(prices_of_product(item))
    keys = {
        f"{plan}_{period}"
        for price_id, (plan, period) in PRODUCT_PLANS.items()
        if price_id.startswith("pri_") and price_id in price_ids
    }
    return sorted(keys)


def evaluate(
    code: str,
    discount: dict | None,
    prices_of_product: Callable[[str], list[str]],
    now: datetime | None = None,
) -> dict:
    """The lookup answer for one code, given what Paddle returned for it.

    Order matters: a code that has expired *and* been used up is reported as expired,
    since that is the one nothing can change.
    """
    now = now or datetime.now(timezone.utc)
    answer: dict = {"code": code, "valid": False, "reason": None}

    # A discount not enabled for checkout can only be applied by our own API calls, so to
    # someone typing it in it does not exist.
    if discount is None or not discount.get("enabled_for_checkout"):
        return {**answer, "reason": "not_found"}

    expires_at = _parse_time(discount.get("expires_at"))
    if expires_at is not None and expires_at <= now:
        return {**answer, "reason": "expired"}

    usage_limit = discount.get("usage_limit")
    if usage_limit is not None and (discount.get("times_used") or 0) >= usage_limit:
        return {**answer, "reason": "used_up"}

    plans = _covered_plans(discount.get("restrict_to"), prices_of_product)
    if plans == []:
        return {**answer, "reason": "not_applicable"}

    kind = discount.get("type")
    return {
        **answer,
        "valid": True,
        "discount_id": discount.get("id"),
        # `flat_per_seat` is `flat` at a quantity of one, which every plan is.
        "type": "percentage" if kind == "percentage" else "flat",
        "amount": discount.get("amount"),
        "currency_code": discount.get("currency_code"),
        "recur": bool(discount.get("recur")),
        "maximum_recurring_intervals": discount.get("maximum_recurring_intervals"),
        "expires_at": discount.get("expires_at"),
        "plans": plans,
    }

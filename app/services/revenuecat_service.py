"""Ingesting RevenueCat webhooks: verify, map, apply.

RevenueCat is the single source of billing events for all three rails — App Store, Play
Billing, and Paddle on the web. That is what it is being paid for: one normalised event
shape instead of Apple's JWS notifications, Google's Pub/Sub RTDN with its acknowledgment
trap, and Paddle's own signature scheme, each with its own state machine to maintain.

**Nothing here decides what a plan permits.** This module translates an event into a row;
`entitlement_service` decides what the row means. Swapping billing provider should rewrite
this file and touch nothing else.

Three properties this code has to hold, because the transport does not:

**Delivery is not exactly-once.** RevenueCat retries up to five times, and a retry carries
the same `event_id`. `subscription_events.event_id` is UNIQUE and checked before applying.

**Delivery is not ordered.** A RENEWAL can arrive after the CANCELLATION that follows it.
Events are compared on `event_timestamp_ms` against the last one applied for that owner,
and a stale event is recorded and discarded rather than applied.

**A payload can name something we do not recognise.** An unmapped product must neither
grant nor revoke: it is recorded as `unmapped` and entitlement is left exactly as it was.
Guessing in either direction is worse than doing nothing and being able to see that it
happened.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from app.clock import utc_now_naive
from app.config import settings
from app.services import entitlement_service as ent

logger = logging.getLogger(__name__)

#: How far out of date a signature timestamp may be. RevenueCat recomputes the timestamp
#: on every retry, so a legitimate delivery is always fresh; a wide window only helps a
#: replay.
SIGNATURE_TOLERANCE = timedelta(minutes=5)

#: RevenueCat store values that mean "the web", i.e. sold through Paddle rather than a
#: mobile store. Kept as a set because RC_BILLING and PADDLE both appear depending on how
#: the web billing engine is configured, and both mean the same thing to us.
_WEB_STORES = frozenset({"PADDLE", "RC_BILLING", "STRIPE"})

#: RevenueCat store -> the `source` recorded on the subscription row. `source` drives
#: where a client sends someone who wants to cancel: an App Store subscription cannot be
#: cancelled by us, and offering a button that cannot work is worse than offering none.
_STORE_TO_SOURCE = {
    "APP_STORE": "apple",
    "MAC_APP_STORE": "apple",
    "PLAY_STORE": "google",
    "AMAZON": "google",
}


def _source_for_store(store: str | None) -> str:
    if not store:
        return "unknown"
    key = store.strip().upper()
    if key in _WEB_STORES:
        return "paddle"
    return _STORE_TO_SOURCE.get(key, "unknown")


# ── Product mapping ──────────────────────────────────────────────────────────
#
# Store product identifiers are created by hand in App Store Connect, Play Console and
# Paddle, so this map is the seam where three consoles meet one codebase. It is explicit
# rather than parsed out of the identifier string: a convention silently broken in a
# console would otherwise resolve to a plausible-looking wrong plan, where an explicit
# table fails loudly as `unmapped` and changes nothing.
#
# The identifiers are not final — the catalog is not built yet (see the subscriptions
# plan §5 Phase 0). Filling this in is what connects it.
PRODUCT_PLANS: dict[str, tuple[str, str]] = {
    "rc_tier_3_8_monthly": (ent.PLAN_TIER_3_8, "monthly"),
    "rc_tier_3_8_yearly": (ent.PLAN_TIER_3_8, "yearly"),
    "rc_tier_9_15_monthly": (ent.PLAN_TIER_9_15, "monthly"),
    "rc_tier_9_15_yearly": (ent.PLAN_TIER_9_15, "yearly"),
    "rc_tier_16_plus_monthly": (ent.PLAN_TIER_16_PLUS, "monthly"),
    "rc_tier_16_plus_yearly": (ent.PLAN_TIER_16_PLUS, "yearly"),
}


# ── Event semantics ──────────────────────────────────────────────────────────
#
# What each event type means for `subscriptions.status`. Types absent from this map carry
# no entitlement meaning (SUBSCRIBER_ALIAS, EXPERIMENT_ENROLLMENT, INVOICE_ISSUANCE and
# friends) and are recorded as `ignored` rather than guessed at.
#
# CANCELLATION maps to `canceled`, not `expired`: a cancellation ends the *next* renewal,
# and access continues to `expiration_at_ms`. Revoking on cancellation would take away a
# period the landlord has already paid for.
EVENT_STATUS: dict[str, str] = {
    "INITIAL_PURCHASE": "active",
    "RENEWAL": "active",
    "UNCANCELLATION": "active",
    "PRODUCT_CHANGE": "active",
    "SUBSCRIPTION_EXTENDED": "active",
    "REFUND_REVERSED": "active",
    "TEMPORARY_ENTITLEMENT_GRANT": "active",
    "NON_RENEWING_PURCHASE": "active",
    "CANCELLATION": "canceled",
    "EXPIRATION": "expired",
    "BILLING_ISSUE": "past_due",
    "SUBSCRIPTION_PAUSED": "paused",
}


@dataclass(frozen=True)
class IngestResult:
    """What happened to one event. `applied` is the audit value stored on the row."""

    applied: str  # applied | ignored | stale | unmapped
    detail: str | None = None
    owner_id: str | None = None


def _ms_to_datetime(value) -> datetime | None:
    """Milliseconds since the epoch -> a naive UTC datetime, or None if unusable.

    Naive-UTC rather than aware, to match `app.clock.utc_now_naive()` and every timestamp
    column in this schema; mixing the two raises on comparison. Built through an aware
    value and then stripped, because `utcfromtimestamp` is deprecated and `fromtimestamp`
    without a tz would read the host's local zone — which is the same class of bug the
    clock module exists to prevent.
    """
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).replace(
            tzinfo=None
        )
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def verify_request(
    signature_header: str | None, authorization: str | None, raw_body: bytes
) -> None:
    """Authenticate one webhook request, or raise.

    RevenueCat offers two independent mechanisms and which one a project exposes varies,
    so both are supported:

    * **HMAC signature** (`REVENUECAT_WEBHOOK_SECRET`) — the stronger one. It proves the
      body arrived unaltered, not merely that the sender knew a string.
    * **Static `Authorization` header** (`REVENUECAT_WEBHOOK_AUTH`) — an opaque shared
      secret, sent verbatim as typed into the dashboard. No `Bearer` scheme is implied.

    **Whichever is configured is required, and if both are configured both must pass.**
    Configuring neither is a 503 rather than an open door: an endpoint that cannot
    authenticate anyone and therefore accepts everyone is a write path into billing state,
    and a misconfiguration there should be loud rather than quietly permissive.
    """
    secret = settings.REVENUECAT_WEBHOOK_SECRET
    expected_auth = settings.REVENUECAT_WEBHOOK_AUTH

    if not secret and not expected_auth:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook authentication is not configured.",
        )

    if expected_auth:
        # compare_digest, not ==: a plain comparison returns at the first differing byte
        # and leaks the value to anyone willing to time it.
        if not authorization or not hmac.compare_digest(authorization, expected_auth):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad authorization"
            )

    if secret:
        _verify_signature(signature_header, raw_body, secret)


def _verify_signature(header: str | None, raw_body: bytes, secret: str) -> None:
    """Verify `X-RevenueCat-Webhook-Signature`.

    The header is `t=<unix>,v1=<hex>` and the signed payload is `"<t>.<raw_body>"`, with
    the body as the exact bytes received. Parsing the JSON first and re-serialising it
    changes the bytes and every signature fails — the classic way to lose a day to this.
    """
    if not header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing signature")

    parts = dict(
        piece.split("=", 1) for piece in header.split(",") if "=" in piece
    )
    timestamp, provided = parts.get("t"), parts.get("v1")
    if not timestamp or not provided:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed signature")

    sent_at = _ms_to_datetime(int(timestamp) * 1000) if timestamp.isdigit() else None
    if sent_at is None or abs(utc_now_naive() - sent_at) > SIGNATURE_TOLERANCE:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signature expired")

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad signature")


class RevenueCatService:
    def __init__(self, event_repository, subscription_repository):
        self.event_repository = event_repository
        self.subscription_repository = subscription_repository

    def ingest(self, body: dict, raw_body: str) -> IngestResult:
        """Apply one webhook event. Safe to call twice with the same event.

        Returns rather than raises for every *business* outcome — an unmapped product, a
        stale event, an event type with no entitlement meaning. Only a malformed request
        is an error. RevenueCat retries anything that is not a 200, and retrying an event
        we have correctly decided to ignore would achieve nothing five times over.
        """
        event = body.get("event") or {}
        event_id = event.get("id")
        event_type = (event.get("type") or "").strip().upper()
        owner_id = event.get("app_user_id")
        store = event.get("store")
        occurred_at = _ms_to_datetime(event.get("event_timestamp_ms"))

        if not event_id:
            raise HTTPException(status_code=400, detail="Event has no id")

        if self.event_repository.exists(event_id):
            # A retry of something already handled. Not an error: RevenueCat retries a
            # delivery it could not confirm, including ones that in fact succeeded.
            return IngestResult("ignored", "duplicate", owner_id)

        result = self._apply(event, event_type, owner_id, store, occurred_at)

        self.event_repository.record(
            event_id=event_id,
            event_type=event_type or "UNKNOWN",
            owner_id=owner_id,
            store=store,
            environment=event.get("environment"),
            occurred_at=occurred_at,
            applied=result.applied,
            detail=result.detail,
            payload=raw_body,
        )
        return result

    def _apply(self, event, event_type, owner_id, store, occurred_at) -> IngestResult:
        if event_type == "TEST":
            return IngestResult("ignored", "test_event", owner_id)

        status_value = EVENT_STATUS.get(event_type)
        if status_value is None:
            return IngestResult("ignored", "no_entitlement_meaning", owner_id)

        if not owner_id:
            logger.warning("RevenueCat %s event carries no app_user_id", event_type)
            return IngestResult("ignored", "no_app_user_id", None)

        existing = self.subscription_repository.get_for_owner(owner_id)
        if (
            existing is not None
            and occurred_at is not None
            and existing.last_event_at is not None
            and occurred_at < existing.last_event_at
        ):
            # Out of order. The newer state is already stored; applying this would move
            # the account backwards to a status that has since been superseded.
            return IngestResult("stale", "older_than_applied", owner_id)

        product_id = event.get("product_id")
        mapped = PRODUCT_PLANS.get(product_id) if product_id else None

        if mapped is None:
            # Neither grant nor revoke. A product nobody has mapped is a console/config
            # problem, and the safe response is to change nothing and make it visible.
            logger.warning(
                "RevenueCat %s event for unmapped product %r (owner %s)",
                event_type,
                product_id,
                owner_id,
            )
            return IngestResult("unmapped", f"product:{product_id}", owner_id)

        plan, period = mapped
        self.subscription_repository.upsert(
            owner_id,
            plan=plan,
            period=period,
            status=status_value,
            source=_source_for_store(store),
            external_id=event.get("original_transaction_id") or event.get("transaction_id"),
            current_period_end=_ms_to_datetime(event.get("expiration_at_ms")),
            grace_until=_ms_to_datetime(event.get("grace_period_expiration_at_ms")),
            price_amount=event.get("price"),
            price_currency=event.get("currency"),
            last_event_at=occurred_at,
        )
        return IngestResult("applied", event_type, owner_id)


def parse_body(raw_body: bytes) -> dict:
    try:
        return json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Body is not valid JSON") from exc

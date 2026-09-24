"""Webhook ingestion: signature, idempotency, ordering, and mapping.

The transport guarantees none of what the billing state needs — deliveries repeat, arrive
out of order, and can name products nobody has mapped. Each of those is tested here
directly, because each one silently corrupts entitlement if it is got wrong and none of
them is visible in normal use.
"""
import hashlib
import hmac
import json
import time

import pytest

from app.config import settings
from app.repositories.subscription_repository import SubscriptionRepository
from app.services import entitlement_service as ent
from app.services import revenuecat_service as rc
from app.services.billing_catalog import PRODUCT_PLANS
from tests.conftest import OWNER_A, OWNER_B

SECRET = "whsec_test_secret"
PRODUCT = "pri_01m39c1sm362n1j2jvd7jhxs32"  # 9–15, monthly


@pytest.fixture
def signing():
    """HMAC signing configured, the static header off."""
    secret = settings.REVENUECAT_WEBHOOK_SECRET
    auth = settings.REVENUECAT_WEBHOOK_AUTH
    settings.REVENUECAT_WEBHOOK_SECRET = SECRET
    settings.REVENUECAT_WEBHOOK_AUTH = ""
    yield
    settings.REVENUECAT_WEBHOOK_SECRET = secret
    settings.REVENUECAT_WEBHOOK_AUTH = auth


def _event(
    event_id="evt_1",
    event_type="INITIAL_PURCHASE",
    product_id=PRODUCT,
    owner_id=OWNER_A,
    timestamp_ms=None,
    store="PADDLE",
    **extra,
):
    event = {
        "id": event_id,
        "type": event_type,
        "app_user_id": owner_id,
        "product_id": product_id,
        "store": store,
        "environment": "PRODUCTION",
        "event_timestamp_ms": timestamp_ms or int(time.time() * 1000),
        "expiration_at_ms": int((time.time() + 30 * 86400) * 1000),
        "price": 20.0,
        "currency": "USD",
    }
    event.update(extra)
    return {"api_version": "1.0", "event": event}


def _post(client, body, *, secret=SECRET, timestamp=None):
    raw = json.dumps(body).encode("utf-8")
    ts = str(timestamp or int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"), f"{ts}.".encode("utf-8") + raw, hashlib.sha256
    ).hexdigest()
    return client.post(
        "/webhooks/revenuecat",
        content=raw,
        headers={
            "X-RevenueCat-Webhook-Signature": f"t={ts},v1={signature}",
            "Content-Type": "application/json",
        },
    )


# ── Signature ────────────────────────────────────────────────────────────────


def test_a_valid_signature_is_accepted(client, signing):
    assert _post(client, _event()).status_code == 200


def test_a_wrong_secret_is_rejected(client, signing):
    response = _post(client, _event(), secret="whsec_not_the_secret")
    assert response.status_code == 401


def test_a_missing_signature_header_is_rejected(client, signing):
    response = client.post("/webhooks/revenuecat", json=_event())
    assert response.status_code == 401


def test_an_old_signature_is_rejected(client, signing):
    """Replay protection. RevenueCat recomputes the timestamp on every retry, so a
    legitimate delivery is always fresh."""
    stale = int(time.time()) - 3600
    assert _post(client, _event(), timestamp=stale).status_code == 401


def test_a_tampered_body_is_rejected(client, signing):
    """The signature covers the exact bytes. Changing the plan after signing must fail —
    otherwise the endpoint is a free upgrade for anyone who has seen one payload."""
    body = _event()
    raw = json.dumps(body).encode("utf-8")
    ts = str(int(time.time()))
    signature = hmac.new(
        SECRET.encode("utf-8"), f"{ts}.".encode("utf-8") + raw, hashlib.sha256
    ).hexdigest()

    body["event"]["product_id"] = "pri_01m39c45cxf0jwj12gb7aq6b7h"
    tampered = json.dumps(body).encode("utf-8")

    response = client.post(
        "/webhooks/revenuecat",
        content=tampered,
        headers={"X-RevenueCat-Webhook-Signature": f"t={ts},v1={signature}"},
    )
    assert response.status_code == 401


def test_no_mechanism_configured_refuses_rather_than_accepts(client):
    """A misconfiguration must be loud. An endpoint that cannot authenticate anyone and
    therefore accepts everyone is a write path into billing state."""
    secret = settings.REVENUECAT_WEBHOOK_SECRET
    auth = settings.REVENUECAT_WEBHOOK_AUTH
    settings.REVENUECAT_WEBHOOK_SECRET = ""
    settings.REVENUECAT_WEBHOOK_AUTH = ""
    try:
        assert _post(client, _event()).status_code == 503
    finally:
        settings.REVENUECAT_WEBHOOK_SECRET = secret
        settings.REVENUECAT_WEBHOOK_AUTH = auth


# ── The static Authorization header, RevenueCat's other option ───────────────


@pytest.fixture
def header_auth():
    """Authorization header configured, HMAC signing off."""
    secret = settings.REVENUECAT_WEBHOOK_SECRET
    auth = settings.REVENUECAT_WEBHOOK_AUTH
    settings.REVENUECAT_WEBHOOK_SECRET = ""
    settings.REVENUECAT_WEBHOOK_AUTH = "rc_shared_value"
    yield
    settings.REVENUECAT_WEBHOOK_SECRET = secret
    settings.REVENUECAT_WEBHOOK_AUTH = auth


def _post_plain(client, body, authorization=None):
    headers = {"Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    return client.post(
        "/webhooks/revenuecat", content=json.dumps(body).encode("utf-8"), headers=headers
    )


def test_a_matching_authorization_header_is_accepted(client, header_auth):
    """Sent verbatim as typed into the dashboard — no Bearer scheme is implied."""
    assert _post_plain(client, _event(), "rc_shared_value").status_code == 200


def test_a_wrong_authorization_header_is_rejected(client, header_auth):
    assert _post_plain(client, _event(), "rc_wrong_value").status_code == 401


def test_a_missing_authorization_header_is_rejected(client, header_auth):
    assert _post_plain(client, _event()).status_code == 401


def test_when_both_are_configured_both_must_pass(client, signing):
    """Belt and braces means both, not either. A valid signature with the wrong header
    is still a rejection."""
    original = settings.REVENUECAT_WEBHOOK_AUTH
    settings.REVENUECAT_WEBHOOK_AUTH = "rc_shared_value"
    try:
        # Correct signature, no Authorization header at all.
        assert _post(client, _event(event_id="evt_both_1")).status_code == 401

        # Correct signature and correct header.
        raw = json.dumps(_event(event_id="evt_both_2")).encode("utf-8")
        ts = str(int(time.time()))
        signature = hmac.new(
            SECRET.encode("utf-8"), f"{ts}.".encode("utf-8") + raw, hashlib.sha256
        ).hexdigest()
        ok = client.post(
            "/webhooks/revenuecat",
            content=raw,
            headers={
                "X-RevenueCat-Webhook-Signature": f"t={ts},v1={signature}",
                "Authorization": "rc_shared_value",
            },
        )
        assert ok.status_code == 200
    finally:
        settings.REVENUECAT_WEBHOOK_AUTH = original


# ── Applying events ──────────────────────────────────────────────────────────


def test_an_initial_purchase_grants_the_plan(client, signing, db_session):
    assert _post(client, _event()).json()["status"] == "applied"

    subscription = SubscriptionRepository(db_session).get_for_owner(OWNER_A)
    assert subscription.plan == ent.PLAN_TIER_9_15
    assert subscription.period == "monthly"
    assert subscription.status == "active"
    assert subscription.source == "paddle"


@pytest.mark.parametrize(
    "store,source",
    [
        ("APP_STORE", "apple"),
        ("MAC_APP_STORE", "apple"),
        ("PLAY_STORE", "google"),
        ("PADDLE", "paddle"),
        ("RC_BILLING", "paddle"),
    ],
)
def test_the_store_decides_the_recorded_source(client, signing, db_session, store, source):
    """`source` is what tells a client where to send someone who wants to cancel. An App
    Store subscription cannot be cancelled by us."""
    _post(client, _event(event_id=f"evt_{store}", store=store))
    assert SubscriptionRepository(db_session).get_for_owner(OWNER_A).source == source


@pytest.mark.parametrize(
    "event_type,expected",
    [
        ("INITIAL_PURCHASE", "active"),
        ("RENEWAL", "active"),
        ("UNCANCELLATION", "active"),
        ("PRODUCT_CHANGE", "active"),
        ("CANCELLATION", "canceled"),
        ("EXPIRATION", "expired"),
        ("BILLING_ISSUE", "past_due"),
        ("SUBSCRIPTION_PAUSED", "paused"),
    ],
)
def test_event_type_maps_to_status(client, signing, db_session, event_type, expected):
    _post(client, _event(event_id=f"evt_{event_type}", event_type=event_type))
    assert SubscriptionRepository(db_session).get_for_owner(OWNER_A).status == expected


def test_cancellation_keeps_access_until_the_period_ends(client, signing, db_session):
    """CANCELLATION is not EXPIRATION. Revoking here would take away a period already
    paid for — and `/refunds` says publicly that it does not."""
    _post(client, _event(event_type="CANCELLATION"))
    subscription = SubscriptionRepository(db_session).get_for_owner(OWNER_A)

    resolved = ent.effective_plan(
        subscription.plan,
        subscription.status,
        subscription.current_period_end,
        subscription.grace_until,
    )
    assert resolved.plan == ent.PLAN_TIER_9_15


# ── Idempotency and ordering ─────────────────────────────────────────────────


def test_the_same_event_delivered_twice_is_applied_once(client, signing, db_session):
    first = _post(client, _event(event_id="evt_same"))
    second = _post(client, _event(event_id="evt_same"))

    assert first.json()["status"] == "applied"
    assert second.json()["status"] == "ignored"
    assert second.status_code == 200  # a retry is not an error


def test_an_out_of_order_event_does_not_move_the_account_backwards(
    client, signing, db_session
):
    """The failure this guards: a RENEWAL delayed in delivery arriving after the
    EXPIRATION that supersedes it would silently restore a lapsed subscription."""
    now_ms = int(time.time() * 1000)

    _post(client, _event(event_id="evt_new", event_type="EXPIRATION", timestamp_ms=now_ms))
    late = _post(
        client,
        _event(
            event_id="evt_old",
            event_type="RENEWAL",
            timestamp_ms=now_ms - 60_000,
        ),
    )

    assert late.json()["status"] == "stale"
    assert SubscriptionRepository(db_session).get_for_owner(OWNER_A).status == "expired"


# ── Payloads that name something unknown ─────────────────────────────────────


def test_an_unmapped_product_changes_nothing(client, signing, db_session):
    """Neither grant nor revoke. A product nobody has mapped is a console misconfiguration
    and the safe answer is to change nothing and leave a record that it happened."""
    _post(client, _event(event_id="evt_good"))
    before = SubscriptionRepository(db_session).get_for_owner(OWNER_A).plan

    response = _post(
        client, _event(event_id="evt_unmapped", product_id="rc_some_new_thing")
    )

    assert response.json()["status"] == "unmapped"
    assert SubscriptionRepository(db_session).get_for_owner(OWNER_A).plan == before


def test_an_event_type_with_no_entitlement_meaning_is_ignored(client, signing):
    response = _post(
        client, _event(event_id="evt_alias", event_type="SUBSCRIBER_ALIAS")
    )
    assert response.json()["status"] == "ignored"


def test_a_test_event_is_acknowledged_and_does_nothing(client, signing, db_session):
    """The dashboard's "send test event" button must not create a subscription."""
    response = _post(client, _event(event_id="evt_test", event_type="TEST"))
    assert response.status_code == 200
    assert SubscriptionRepository(db_session).get_for_owner(OWNER_A) is None


def test_an_event_with_no_id_is_a_bad_request(client, signing):
    body = _event()
    del body["event"]["id"]
    assert _post(client, body).status_code == 400


def test_every_event_is_recorded_even_when_not_applied(client, signing, db_session):
    from app.models.subscription_event import SubscriptionEvent

    _post(client, _event(event_id="evt_rec", product_id="rc_unknown"))

    recorded = (
        db_session.query(SubscriptionEvent)
        .filter(SubscriptionEvent.event_id == "evt_rec")
        .one()
    )
    assert recorded.applied == "unmapped"
    assert recorded.owner_id == OWNER_A
    assert json.loads(recorded.payload)["event"]["id"] == "evt_rec"


# ── The catalog ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "product_id,plan,period",
    [
        ("pri_01m39bzfjqd489x258g3srtg9v", ent.PLAN_TIER_3_8, "monthly"),
        ("pri_01m39c0e8mx96spd6sagf972ny", ent.PLAN_TIER_3_8, "yearly"),
        ("pri_01m39c1sm362n1j2jvd7jhxs32", ent.PLAN_TIER_9_15, "monthly"),
        ("pri_01m39c2j8af8j4vd49n2t5hsm6", ent.PLAN_TIER_9_15, "yearly"),
        ("pri_01m39c3qa4597sp255tbbhes17", ent.PLAN_TIER_16_PLUS, "monthly"),
        ("pri_01m39c45cxf0jwj12gb7aq6b7h", ent.PLAN_TIER_16_PLUS, "yearly"),
    ],
)
def test_each_paddle_price_grants_its_band_and_period(
    client, signing, db_session, product_id, plan, period
):
    _post(client, _event(event_id=f"evt_{product_id}", product_id=product_id))
    subscription = SubscriptionRepository(db_session).get_for_owner(OWNER_A)
    assert (subscription.plan, subscription.period) == (plan, period)


def test_every_mapped_plan_is_a_paid_plan():
    """A typo in the catalog must not map a price to a plan the rules don't know."""
    paid = {ent.PLAN_TIER_3_8, ent.PLAN_TIER_9_15, ent.PLAN_TIER_16_PLUS}
    for plan, period in PRODUCT_PLANS.values():
        assert plan in paid
        assert period in {"monthly", "yearly"}


def test_the_entitlement_identifiers_are_the_plan_names():
    """RevenueCat's entitlements are named after the plans, so the dashboard and the
    database talk about the same thing. Renaming a plan here breaks that silently."""
    assert (ent.PLAN_TIER_3_8, ent.PLAN_TIER_9_15, ent.PLAN_TIER_16_PLUS) == (
        "tier_3_8",
        "tier_9_15",
        "tier_16_plus",
    )


# ── Access over a subscription's life ────────────────────────────────────────


def _resolved(db_session, owner=OWNER_A):
    s = SubscriptionRepository(db_session).get_for_owner(owner)
    return ent.effective_plan(s.plan, s.status, s.current_period_end, s.grace_until).plan


@pytest.mark.parametrize(
    "event_type,has_access",
    [
        ("INITIAL_PURCHASE", True),
        ("RENEWAL", True),
        ("PRODUCT_CHANGE", True),
        ("UNCANCELLATION", True),
        ("CANCELLATION", True),  # until the paid period ends
        ("EXPIRATION", False),
    ],
)
def test_which_events_leave_access_in_place(
    client, signing, db_session, event_type, has_access
):
    _post(client, _event(event_id=f"evt_{event_type}", event_type=event_type))
    assert (_resolved(db_session) == ent.PLAN_TIER_9_15) is has_access


def test_a_billing_issue_keeps_access_only_through_the_grace_period(
    client, signing, db_session
):
    now = time.time()
    _post(
        client,
        _event(
            event_id="evt_grace",
            event_type="BILLING_ISSUE",
            grace_period_expiration_at_ms=int((now + 3 * 86400) * 1000),
        ),
    )
    assert _resolved(db_session) == ent.PLAN_TIER_9_15

    _post(
        client,
        _event(
            event_id="evt_no_grace",
            event_type="BILLING_ISSUE",
            timestamp_ms=int((now + 1) * 1000),
            grace_period_expiration_at_ms=None,
        ),
    )
    assert _resolved(db_session) == ent.PLAN_FREE


def test_a_cancellation_ends_access_once_the_period_is_over(client, signing, db_session):
    _post(
        client,
        _event(
            event_type="CANCELLATION",
            expiration_at_ms=int((time.time() - 60) * 1000),
        ),
    )
    assert _resolved(db_session) == ent.PLAN_FREE


def test_a_product_change_moves_to_the_new_band(client, signing, db_session):
    now_ms = int(time.time() * 1000)
    _post(client, _event(event_id="evt_start", timestamp_ms=now_ms - 1000))
    _post(
        client,
        _event(
            event_id="evt_upgrade",
            event_type="PRODUCT_CHANGE",
            product_id="pri_01m39c3qa4597sp255tbbhes17",
            timestamp_ms=now_ms,
        ),
    )
    assert _resolved(db_session) == ent.PLAN_TIER_16_PLUS


def test_an_uncancellation_restores_renewal(client, signing, db_session):
    now_ms = int(time.time() * 1000)
    _post(
        client,
        _event(event_id="evt_c", event_type="CANCELLATION", timestamp_ms=now_ms - 1000),
    )
    _post(client, _event(event_id="evt_u", event_type="UNCANCELLATION", timestamp_ms=now_ms))
    assert SubscriptionRepository(db_session).get_for_owner(OWNER_A).status == "active"


# ── Transfer ─────────────────────────────────────────────────────────────────


def _transfer(event_id, sources, targets, timestamp_ms=None):
    body = _event(
        event_id=event_id,
        event_type="TRANSFER",
        owner_id=None,
        product_id=None,
        timestamp_ms=timestamp_ms,
    )
    body["event"]["transferred_from"] = sources
    body["event"]["transferred_to"] = targets
    return body


def test_a_transfer_moves_the_subscription_to_the_new_account(client, signing, db_session):
    now_ms = int(time.time() * 1000)
    _post(client, _event(event_id="evt_buy", timestamp_ms=now_ms - 1000))

    response = _post(client, _transfer("evt_move", [OWNER_A], [OWNER_B], now_ms))

    assert response.json()["status"] == "applied"
    repo = SubscriptionRepository(db_session)
    assert repo.get_for_owner(OWNER_A) is None
    assert repo.get_for_owner(OWNER_B).plan == ent.PLAN_TIER_9_15


def test_a_transfer_replaces_what_the_target_had(client, signing, db_session):
    now_ms = int(time.time() * 1000)
    _post(client, _event(event_id="evt_a", timestamp_ms=now_ms - 2000))
    _post(
        client,
        _event(
            event_id="evt_b",
            owner_id=OWNER_B,
            product_id="pri_01m39bzfjqd489x258g3srtg9v",
            timestamp_ms=now_ms - 1000,
        ),
    )

    _post(client, _transfer("evt_move", [OWNER_A], [OWNER_B], now_ms))

    assert SubscriptionRepository(db_session).get_for_owner(OWNER_B).plan == ent.PLAN_TIER_9_15


def test_a_transfer_with_nothing_to_move_changes_nothing(client, signing, db_session):
    response = _post(client, _transfer("evt_move", [OWNER_A], [OWNER_B]))
    assert response.json()["status"] == "ignored"
    assert SubscriptionRepository(db_session).get_for_owner(OWNER_B) is None


def test_a_transfer_to_an_anonymous_id_changes_nothing(client, signing, db_session):
    _post(client, _event(event_id="evt_buy"))
    response = _post(client, _transfer("evt_move", [OWNER_A], ["$RCAnonymousID:abc"]))
    assert response.json()["status"] == "ignored"
    assert SubscriptionRepository(db_session).get_for_owner(OWNER_A) is not None


# ── Events that must not touch a real account ────────────────────────────────


def test_a_sandbox_event_is_recorded_but_not_applied(client, signing, db_session):
    from app.models.subscription_event import SubscriptionEvent

    body = _event(event_id="evt_sandbox")
    body["event"]["environment"] = "SANDBOX"
    response = _post(client, body)

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert SubscriptionRepository(db_session).get_for_owner(OWNER_A) is None
    recorded = db_session.query(SubscriptionEvent).filter_by(event_id="evt_sandbox").one()
    assert (recorded.environment, recorded.detail) == ("SANDBOX", "sandbox")


def test_sandbox_events_apply_when_the_backend_is_told_to(client, signing, db_session):
    original = settings.REVENUECAT_APPLY_SANDBOX
    settings.REVENUECAT_APPLY_SANDBOX = True
    try:
        body = _event(event_id="evt_sandbox")
        body["event"]["environment"] = "SANDBOX"
        assert _post(client, body).json()["status"] == "applied"
    finally:
        settings.REVENUECAT_APPLY_SANDBOX = original


def test_an_anonymous_app_user_id_is_not_applied(client, signing, db_session):
    """A checkout that did not start in the app belongs to no account."""
    anonymous = "$RCAnonymousID:0123456789abcdef"
    response = _post(client, _event(owner_id=anonymous))
    assert response.json()["status"] == "ignored"
    assert SubscriptionRepository(db_session).get_for_owner(anonymous) is None


def test_an_unknown_event_type_is_acknowledged(client, signing, db_session):
    response = _post(client, _event(event_type="SOMETHING_NEW_FROM_REVENUECAT"))
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert SubscriptionRepository(db_session).get_for_owner(OWNER_A) is None

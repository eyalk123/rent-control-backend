"""Web (Paddle) subscriptions: the manage links, changing plan, and cancelling on deletion.

Paddle itself is never called: `app.services.paddle_api` is replaced per test, so what is
asserted is what the backend asks of Paddle and what it does with the answer.
"""
from datetime import datetime

import pytest

from app.repositories.subscription_repository import SubscriptionRepository
from app.services import entitlement_service as ent
from app.services import paddle_api
from tests.conftest import OWNER_A

SUB_ID = "sub_01m466rzzezw4yqnzxk3xd26kx"


def _web_subscription(db, status="active", source="paddle", external_id=SUB_ID):
    return SubscriptionRepository(db).upsert(
        OWNER_A,
        plan=ent.PLAN_TIER_3_8,
        period="monthly",
        status=status,
        source=source,
        external_id=external_id,
        current_period_end=datetime(2027, 1, 1),
        price_currency="USD",
        price_amount=15,
    )


@pytest.fixture
def paddle(monkeypatch):
    """Records each Paddle call; `state['status']` is what Paddle reports."""
    calls: list[tuple] = []
    state = {"status": "active", "fail": False}

    def _maybe_fail():
        if state["fail"]:
            raise paddle_api.PaddleError("paddle down")

    def get_subscription(sub_id):
        calls.append(("get", sub_id))
        _maybe_fail()
        return {
            "status": state["status"],
            "management_urls": {
                "cancel": "https://paddle.example/cancel?token=t",
                "update_payment_method": "https://paddle.example/pay?token=t",
            },
        }

    def cancel_now(sub_id):
        calls.append(("cancel", sub_id))
        _maybe_fail()
        return {"status": "canceled"}

    def change_price(sub_id, price_id):
        calls.append(("change", sub_id, price_id))
        _maybe_fail()
        return {"current_billing_period": {"ends_at": "2027-02-01T00:00:00Z"}}

    monkeypatch.setattr(paddle_api, "get_subscription", get_subscription)
    monkeypatch.setattr(paddle_api, "cancel_now", cancel_now)
    monkeypatch.setattr(paddle_api, "change_price", change_price)
    return {"calls": calls, "state": state}


# ── Manage links ─────────────────────────────────────────────────────────────


def test_manage_links_come_from_paddle(client, db_session, paddle):
    _web_subscription(db_session)

    response = client.get("/subscription/manage-links")

    assert response.status_code == 200
    assert response.json() == {
        "cancel_url": "https://paddle.example/cancel?token=t",
        "update_payment_method_url": "https://paddle.example/pay?token=t",
    }


@pytest.mark.parametrize("source", ["apple", "google"])
def test_manage_links_refuse_a_store_subscription(client, db_session, paddle, source):
    _web_subscription(db_session, source=source, external_id="2000000123")
    assert client.get("/subscription/manage-links").status_code == 409
    assert paddle["calls"] == []


def test_manage_links_without_a_subscription_are_refused(client, paddle):
    assert client.get("/subscription/manage-links").status_code == 409


def test_manage_links_report_a_paddle_failure_as_502(client, db_session, paddle):
    _web_subscription(db_session)
    paddle["state"]["fail"] = True
    assert client.get("/subscription/manage-links").status_code == 502


# ── Change plan ──────────────────────────────────────────────────────────────


def test_change_plan_moves_the_paddle_price_and_the_plan_at_once(client, db_session, paddle):
    _web_subscription(db_session)

    response = client.post("/subscription/change-plan", json={"plan": "tier_9_15", "period": "monthly"})

    assert response.status_code == 200
    assert paddle["calls"] == [("change", SUB_ID, "pri_01m39c1sm362n1j2jvd7jhxs32")]
    row = SubscriptionRepository(db_session).get_for_owner(OWNER_A)
    assert (row.plan, row.period, row.status) == (ent.PLAN_TIER_9_15, "monthly", "active")
    assert row.current_period_end == datetime(2027, 2, 1)
    # The response is the new state, so the client can redraw without a second call.
    assert response.json()["plan"] == ent.PLAN_TIER_9_15


@pytest.mark.parametrize("period", ["monthly", "yearly"])
def test_change_plan_refuses_anything_but_a_bigger_plan(client, db_session, paddle, period):
    """A smaller plan applies at renewal (refund policy), which Paddle cannot schedule."""
    _web_subscription(db_session)
    response = client.post("/subscription/change-plan", json={"plan": "tier_3_8", "period": period})
    assert response.status_code == 409
    assert response.json()["detail"] == "not_an_upgrade"
    assert paddle["calls"] == []


def test_change_plan_refuses_a_cancelling_subscription(client, db_session, paddle):
    _web_subscription(db_session, status="canceled")
    response = client.post("/subscription/change-plan", json={"plan": "tier_9_15", "period": "monthly"})
    assert response.status_code == 409
    assert response.json()["detail"] == "subscription_not_active"


def test_change_plan_refuses_a_store_subscription(client, db_session, paddle):
    _web_subscription(db_session, source="apple", external_id="2000000123")
    response = client.post("/subscription/change-plan", json={"plan": "tier_9_15", "period": "monthly"})
    assert response.status_code == 409
    assert paddle["calls"] == []


def test_a_failed_change_leaves_the_plan_alone(client, db_session, paddle):
    _web_subscription(db_session)
    paddle["state"]["fail"] = True

    response = client.post("/subscription/change-plan", json={"plan": "tier_9_15", "period": "monthly"})

    assert response.status_code == 502
    assert SubscriptionRepository(db_session).get_for_owner(OWNER_A).plan == ent.PLAN_TIER_3_8


# ── Account deletion ─────────────────────────────────────────────────────────


def test_deleting_the_account_cancels_the_web_subscription(client, db_session, paddle):
    _web_subscription(db_session)

    assert client.delete("/users/me").status_code == 200
    assert ("cancel", SUB_ID) in paddle["calls"]


def test_deletion_skips_a_subscription_paddle_already_ended(client, db_session, paddle):
    _web_subscription(db_session, status="canceled")
    paddle["state"]["status"] = "canceled"

    assert client.delete("/users/me").status_code == 200
    assert ("cancel", SUB_ID) not in paddle["calls"]


def test_deletion_stops_when_the_web_subscription_cannot_be_cancelled(client, db_session, paddle):
    """Erasing an account Paddle keeps charging for is worse than asking to retry."""
    from app.models.owner import Owner

    db_session.add(Owner(id=OWNER_A, email="a@b.c"))
    db_session.commit()
    _web_subscription(db_session)
    paddle["state"]["fail"] = True

    response = client.delete("/users/me")

    assert response.status_code == 502
    assert response.json()["detail"] == "subscription_cancel_failed"
    db_session.expire_all()
    assert db_session.get(Owner, OWNER_A) is not None


@pytest.mark.parametrize("source", ["apple", "google"])
def test_deletion_does_not_touch_paddle_for_a_store_subscription(client, db_session, paddle, source):
    _web_subscription(db_session, source=source, external_id="2000000123")
    assert client.delete("/users/me").status_code == 200
    assert paddle["calls"] == []


def test_deletion_keeps_the_subscription_row(client, db_session, paddle):
    """Kept so a subscription still being paid for can follow the landlord to a new
    account; it names no one."""
    _web_subscription(db_session, source="apple", external_id="2000000123")
    client.delete("/users/me")
    assert SubscriptionRepository(db_session).get_for_owner(OWNER_A) is not None

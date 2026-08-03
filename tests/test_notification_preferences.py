"""API tests for the notification feed and the preferences/rules endpoints."""
from datetime import date, timedelta

from freezegun import freeze_time

from tests.factories import make_property, make_renter

TODAY = date(2026, 6, 15)


def _seed_expiring(db_session, days_out=40, **kw):
    prop = make_property(db_session, **kw)
    make_renter(
        db_session,
        property_id=prop.id,
        first_name="Soon",
        lease_start=TODAY - timedelta(days=300),
        lease_end=TODAY + timedelta(days=days_out),
        payment_day_of_month=28,  # not yet due -> isolates the lease_expiring case
    )
    return prop


def _seed_overdue(db_session, payment_day=10, **kw):
    prop = make_property(db_session, **kw)
    make_renter(
        db_session,
        property_id=prop.id,
        first_name="Late",
        lease_start=TODAY - timedelta(days=100),
        lease_end=TODAY + timedelta(days=200),
        payment_day_of_month=payment_day,  # 5 days overdue -> default offsets [0, 3] both fire
    )
    return prop


# ── feed ──────────────────────────────────────────────────────────────────

@freeze_time("2026-06-15")
def test_feed_generates_on_read_and_enriches(client, db_session):
    _seed_expiring(db_session, days_out=40)

    resp = client.get("/notifications")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    item = items[0]
    assert item["type"] == "lease_expiring"
    assert item["first_name"] == "Soon"
    assert item["property_address"] == "1 Main St"
    assert item["read"] is False
    assert item["data"]["days_until_expiry"] == 40


@freeze_time("2026-06-15")
def test_feed_mark_read_and_unread_filter(client, db_session):
    _seed_expiring(db_session, days_out=40)
    item_id = client.get("/notifications").json()[0]["id"]

    assert client.post(f"/notifications/{item_id}/read").status_code == 204
    assert client.get("/notifications", params={"status": "unread"}).json() == []
    assert client.get("/notifications").json()[0]["read"] is True


@freeze_time("2026-06-15")
def test_feed_dismiss_hides_item(client, db_session):
    _seed_expiring(db_session, days_out=40)
    item_id = client.get("/notifications").json()[0]["id"]

    assert client.post(f"/notifications/{item_id}/dismiss").status_code == 204
    # Re-reading regenerates candidates, but the dismissed row stays hidden.
    assert client.get("/notifications").json() == []


@freeze_time("2026-06-15")
def test_feed_collapses_duplicate_offsets(client, db_session):
    """Default rent-due offsets [0, 3] both fire for a renter 5 days overdue, but
    the feed shows a single collapsed item (the most-urgent, latest offset)."""
    _seed_overdue(db_session, payment_day=10)
    overdue = [i for i in client.get("/notifications").json() if i["type"] == "overdue"]
    assert len(overdue) == 1
    assert overdue[0]["offset"] == 3


@freeze_time("2026-06-15")
def test_feed_dismiss_clears_whole_group(client, db_session):
    """Dismissing the collapsed item clears every offset behind it, so the renter
    doesn't reappear on the next read."""
    _seed_overdue(db_session, payment_day=10)
    item_id = client.get("/notifications").json()[0]["id"]
    assert client.post(f"/notifications/{item_id}/dismiss").status_code == 204
    assert client.get("/notifications").json() == []


# ── preferences + rules ─────────────────────────────────────────────────────

def test_get_preferences_defaults(client):
    body = client.get("/notification-preferences").json()
    assert body["settings"]["master_enabled"] is True
    assert body["settings"]["muted_events"] == []
    assert body["rules"] == []


@freeze_time("2026-06-15")
def test_mute_event_suppresses_feed(client, db_session):
    _seed_expiring(db_session, days_out=40)
    resp = client.put(
        "/notification-preferences/settings", json={"muted_events": ["lease_expiring"]}
    )
    assert resp.status_code == 200
    assert resp.json()["muted_events"] == ["lease_expiring"]
    assert client.get("/notifications").json() == []


def test_rule_crud(client):
    created = client.post("/notification-rules", json={
        "event_type": "lease_expiring",
        "label": "Cohen renewals",
        "offsets": [90, 60, 30],
        "scope_property_owners": ["Cohen"],
    })
    assert created.status_code == 201
    rule = created.json()
    assert rule["offsets"] == [90, 60, 30]
    assert rule["scope_property_owners"] == ["Cohen"]

    rule_id = rule["id"]
    patched = client.patch(f"/notification-rules/{rule_id}", json={"enabled": False})
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False

    assert any(r["id"] == rule_id for r in client.get("/notification-preferences").json()["rules"])
    assert client.delete(f"/notification-rules/{rule_id}").status_code == 204
    assert client.get("/notification-preferences").json()["rules"] == []


@freeze_time("2026-06-15")
def test_custom_rule_replaces_default_in_feed(client, db_session):
    _seed_expiring(db_session, days_out=40)
    # A tighter custom rule (10 days) means a lease 40 days out no longer alerts.
    client.post("/notification-rules", json={
        "event_type": "lease_expiring",
        "offsets": [10],
    })
    assert client.get("/notifications").json() == []


@freeze_time("2026-06-15")
def test_rule_preview(client, db_session):
    _seed_expiring(db_session, days_out=40)
    resp = client.post("/notification-rules/preview", json={
        "event_type": "lease_expiring",
        "offsets": [90, 30],
    })
    assert resp.status_code == 200
    assert resp.json() == {"matched_renters": 1, "estimated_alerts": 2}


# ── CPI change: mute + threshold, but no rules ───────────────────────────────

def test_cpi_threshold_defaults_and_round_trips(client):
    settings = client.get("/notification-preferences").json()["settings"]
    assert settings["cpi_min_change_amount"] == 10.0
    assert settings["cpi_min_change_percent"] == 0.5

    resp = client.put(
        "/notification-preferences/settings",
        json={"cpi_min_change_amount": 50, "cpi_min_change_percent": 1.5},
    )
    assert resp.status_code == 200
    assert resp.json()["cpi_min_change_amount"] == 50.0
    assert resp.json()["cpi_min_change_percent"] == 1.5
    # ...and the untouched fields survive the partial update.
    assert resp.json()["master_enabled"] is True


def test_cpi_threshold_rejects_a_negative(client):
    resp = client.put(
        "/notification-preferences/settings", json={"cpi_min_change_amount": -5}
    )
    assert resp.status_code == 422


def test_cpi_change_cannot_have_rules(client):
    """Offsets and scope have nothing to say about 'the index moved'. The clients hide
    the event; this stops a hand-rolled request creating a rule the engine ignores."""
    resp = client.post(
        "/notification-rules", json={"event_type": "cpi_rent_change", "offsets": [30]}
    )
    assert resp.status_code == 400
    assert client.get("/notification-preferences").json()["rules"] == []

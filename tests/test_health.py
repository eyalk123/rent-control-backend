def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_seed_and_list_property_smoke(client, db_session):
    """Sanity check: factory-seeded data is visible through the API."""
    from tests.factories import make_property

    make_property(db_session, address="42 Test Ave")
    resp = client.get("/properties")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["address"] == "42 Test Ave"

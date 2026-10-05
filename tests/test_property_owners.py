"""Full-stack tests for /property-owners and the owner link on /properties."""
import json

from app.models.notification import NotificationTypeEnum
from app.models.notification_rule import NotificationRule
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_property, make_property_owner

_PROPERTY = {"address": "1 Main St", "city": "Tel Aviv", "type": "apartment"}


def test_create_and_read_owner_with_contact_details(client):
    resp = client.post(
        "/property-owners",
        json={"name": "  Dad ", "phone": "050-1234567", "email": "dad@example.com",
              "bank_account": "12-345-678901"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Dad"
    assert body["bank_account"] == "12-345-678901"
    assert body["is_active"] is True
    assert body["property_count"] == 0
    assert client.get(f"/property-owners/{body['id']}").json()["phone"] == "050-1234567"


def test_blank_name_rejected(client):
    assert client.post("/property-owners", json={"name": "   "}).status_code == 422


def test_duplicate_name_conflicts_but_a_different_case_is_another_owner(client, db_session):
    make_property_owner(db_session, name="Dad")
    assert client.post("/property-owners", json={"name": "Dad"}).status_code == 409
    # Kept exactly as typed — nothing decides these are the same person.
    assert client.post("/property-owners", json={"name": "dad"}).status_code == 201


def test_list_counts_properties_and_hides_inactive(client, db_session):
    dad = make_property_owner(db_session, name="Dad")
    make_property_owner(db_session, name="Old", is_active=False)
    make_property(db_session, property_owner="Dad")
    make_property(db_session, property_owner="Dad")

    listed = client.get("/property-owners").json()
    assert [(o["name"], o["property_count"]) for o in listed] == [("Dad", 2)]
    assert listed[0]["id"] == dad.id
    every = client.get("/property-owners", params={"include_inactive": True}).json()
    assert {o["name"] for o in every} == {"Dad", "Old"}


def test_owners_are_scoped_to_the_account(client_factory, db_session):
    theirs = make_property_owner(db_session, owner_id=OWNER_B, name="Theirs")
    client_a = client_factory(OWNER_A)
    assert client_a.get("/property-owners").json() == []
    assert client_a.get(f"/property-owners/{theirs.id}").status_code == 404
    assert client_a.patch(f"/property-owners/{theirs.id}", json={"name": "x"}).status_code == 404
    assert client_a.delete(f"/property-owners/{theirs.id}").status_code == 404
    # The same name is free in another account.
    assert client_a.post("/property-owners", json={"name": "Theirs"}).status_code == 201


def test_update_clears_optional_fields_and_ignores_an_empty_name(client, db_session):
    dad = make_property_owner(db_session, name="Dad", phone="050")
    body = client.patch(f"/property-owners/{dad.id}", json={"phone": None, "name": ""}).json()
    assert body["phone"] is None
    assert body["name"] == "Dad"


def test_rename_moves_the_properties_and_notification_scopes(client, db_session):
    dad = make_property_owner(db_session, name="Dad")
    prop = make_property(db_session, property_owner="Dad")
    db_session.add(NotificationRule(
        owner_id=OWNER_A,
        event_type=NotificationTypeEnum.OVERDUE,
        offsets="[0]",
        scope_property_owners=json.dumps(["Dad", "Mom"]),
    ))
    db_session.commit()

    resp = client.patch(f"/property-owners/{dad.id}", json={"name": "Dad (Haifa)"})
    assert resp.status_code == 200
    assert client.get(f"/properties/{prop.id}").json()["property_owner"] == "Dad (Haifa)"
    rule = db_session.query(NotificationRule).filter_by(owner_id=OWNER_A).one()
    db_session.refresh(rule)
    assert json.loads(rule.scope_property_owners) == ["Dad (Haifa)", "Mom"]


def test_rename_onto_an_existing_name_conflicts(client, db_session):
    make_property_owner(db_session, name="Mom")
    dad = make_property_owner(db_session, name="Dad")
    assert client.patch(f"/property-owners/{dad.id}", json={"name": "Mom"}).status_code == 409


def test_delete_refused_while_properties_point_at_the_owner(client, db_session):
    dad = make_property_owner(db_session, name="Dad")
    prop = make_property(db_session, property_owner="Dad")
    assert client.delete(f"/property-owners/{dad.id}").status_code == 409

    client.patch(f"/properties/{prop.id}", json={"property_owner_id": None})
    assert client.delete(f"/property-owners/{dad.id}").status_code == 204
    assert client.get(f"/property-owners/{dad.id}").status_code == 404


# --- the link on /properties -------------------------------------------------------------


def test_property_created_with_an_owner_id(client, db_session):
    dad = make_property_owner(db_session, name="Dad")
    body = client.post("/properties", json={**_PROPERTY, "property_owner_id": dad.id}).json()
    assert body["property_owner_id"] == dad.id
    assert body["property_owner"] == "Dad"


def test_another_accounts_owner_id_is_rejected(client, db_session):
    theirs = make_property_owner(db_session, owner_id=OWNER_B, name="Theirs")
    resp = client.post("/properties", json={**_PROPERTY, "property_owner_id": theirs.id})
    assert resp.status_code == 400


def test_legacy_name_is_matched_exactly_or_creates_an_owner(client, db_session):
    """What mobile builds already in the stores send."""
    dad = make_property_owner(db_session, name="Dad")
    first = client.post("/properties", json={**_PROPERTY, "property_owner": "Dad"}).json()
    assert first["property_owner_id"] == dad.id

    second = client.post("/properties", json={**_PROPERTY, "property_owner": " Mom "}).json()
    assert second["property_owner"] == "Mom"
    names = {o["name"] for o in client.get("/property-owners").json()}
    assert names == {"Dad", "Mom"}


def test_legacy_name_on_update_and_clearing(client, db_session):
    prop = make_property(db_session)
    body = client.patch(f"/properties/{prop.id}", json={"property_owner": "Dad"}).json()
    assert body["property_owner"] == "Dad"
    body = client.patch(f"/properties/{prop.id}", json={"property_owner": None}).json()
    assert body["property_owner"] is None
    assert body["property_owner_id"] is None


def test_update_without_owner_fields_leaves_the_owner_alone(client, db_session):
    prop = make_property(db_session, property_owner="Dad")
    body = client.patch(f"/properties/{prop.id}", json={"city": "Haifa"}).json()
    assert body["property_owner"] == "Dad"


def test_name_filters_still_work_through_the_record(client, db_session):
    """Reports, notification scopes and filters still speak in names."""
    make_property(db_session, property_owner="Dad")
    make_property(db_session, property_owner="Mom")
    from app.models.property import Property

    rows = db_session.query(Property).filter(Property.property_owner.in_(["Mom"])).all()
    assert [p.property_owner for p in rows] == ["Mom"]


def test_account_deletion_removes_owners(client, db_session):
    """Owner records hold contact and bank details — personal data that must not outlive
    the account."""
    from app.models.property_owner import PropertyOwner

    make_property(db_session, property_owner="Dad")
    make_property_owner(db_session, owner_id=OWNER_B, name="Kept")

    assert client.delete("/users/me").status_code == 200

    db_session.expire_all()
    assert [o.name for o in db_session.query(PropertyOwner).all()] == ["Kept"]

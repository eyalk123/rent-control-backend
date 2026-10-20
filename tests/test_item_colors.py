"""Item colors on properties, renters and expense categories (app/entity_colors.py)."""
from app.entity_colors import ENTITY_COLORS, least_used_color
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_expense_category, make_property, make_transaction

PROPERTY = {"address": "10 Herzl St", "city": "Haifa", "type": "apartment"}


def _renter(**overrides):
    payload = {
        "first_name": "Tamar",
        "last_name": "Katz",
        "phone": "0543182207",
        "lease_years": [{"amount": 4600, "type": "contract"}],
    }
    payload.update(overrides)
    return payload


def test_least_used_goes_in_order_then_fills_gaps():
    assert least_used_color([]) == "sky"
    assert least_used_color(["sky", "rose"]) == "olive"
    assert least_used_color(list(ENTITY_COLORS)) == "sky"
    # A gap left by a deleted item is filled before anything gets a second use.
    assert least_used_color(["sky", "olive", "indigo", "plum", "mustard"]) == "rose"
    # Rows predating colors and unknown keys do not count.
    assert least_used_color([None, "teal"]) == "sky"


def test_new_properties_get_six_different_colors(client):
    colors = [client.post("/properties", json=PROPERTY).json()["color"] for _ in range(7)]
    assert colors == ["sky", "rose", "olive", "indigo", "plum", "mustard", "sky"]


def test_colors_are_counted_per_owner(client_factory, db_session):
    make_property(db_session, owner_id=OWNER_B, color="sky")
    client = client_factory(OWNER_A)
    assert client.post("/properties", json=PROPERTY).json()["color"] == "sky"


def test_property_color_can_be_chosen_and_changed_but_not_cleared(client):
    created = client.post("/properties", json={**PROPERTY, "color": "plum"}).json()
    assert created["color"] == "plum"

    pid = created["id"]
    assert client.patch(f"/properties/{pid}", json={"color": "olive"}).json()["color"] == "olive"
    assert client.patch(f"/properties/{pid}", json={"color": None}).json()["color"] == "olive"
    assert client.patch(f"/properties/{pid}", json={"address": "12 Herzl St"}).json()["color"] == "olive"


def test_unknown_color_is_rejected(client):
    assert client.post("/properties", json={**PROPERTY, "color": "teal"}).status_code == 422
    assert client.post("/renters", json=_renter(color="#FF0000")).status_code == 422


def test_renters_get_their_own_sequence(client, db_session):
    make_property(db_session, color="sky")
    first = client.post("/renters", json=_renter()).json()
    second = client.post("/renters", json=_renter(first_name="Noa")).json()
    assert (first["color"], second["color"]) == ("sky", "rose")

    rid = first["id"]
    assert client.patch(f"/renters/{rid}", json={"color": "mustard"}).json()["color"] == "mustard"


def test_renter_carries_its_property_color(client, db_session):
    prop = make_property(db_session, color="indigo")
    body = client.post("/renters", json=_renter(property_id=prop.id)).json()
    assert body["property"]["color"] == "indigo"


def test_new_category_is_spread_against_the_built_ins(client, db_session):
    for key, color in [("water", "sky"), ("repairs", "rose"), ("gas", "olive")]:
        cat = make_expense_category(db_session, owner_id=None, key=key, name=None)
        cat.color = color
    db_session.commit()

    body = client.post("/expense-categories", json={"name": "Pest control"}).json()
    assert body["color"] == "indigo"
    listed = {c["key"] or c["name"]: c["color"] for c in client.get("/expense-categories").json()}
    assert listed["water"] == "sky"


def test_transaction_row_carries_the_property_color(client, db_session):
    prop = make_property(db_session, color="rose")
    make_transaction(db_session, property_id=prop.id)
    rows = client.get("/transactions").json()
    rows = rows["items"] if isinstance(rows, dict) else rows
    assert rows[0]["property_color"] == "rose"

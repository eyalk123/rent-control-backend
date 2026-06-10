"""Full-stack tests for /expense-categories."""
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_expense_category


def test_create_expense_category(client):
    resp = client.post("/expense-categories", json={"name": "Gardening"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Gardening"
    assert body["is_active"] is True


def test_create_expense_category_blank_rejected(client):
    assert client.post("/expense-categories", json={"name": "  "}).status_code == 422


def test_create_assigns_incrementing_sort_order(client, db_session):
    make_expense_category(db_session, name="Existing", sort_order=5)
    body = client.post("/expense-categories", json={"name": "New"}).json()
    assert body["sort_order"] == 6  # max(5) + 1


def test_list_merges_predefined_and_user_ordered(client_factory, db_session):
    make_expense_category(db_session, owner_id=None, key="utilities", name="Utilities", sort_order=1)
    make_expense_category(db_session, owner_id=OWNER_A, name="Mine", sort_order=2)
    make_expense_category(db_session, owner_id=OWNER_B, name="Theirs", sort_order=3)

    client_a = client_factory(OWNER_A)
    body = client_a.get("/expense-categories").json()
    names = [c["name"] for c in body]
    assert names == ["Utilities", "Mine"]  # predefined + own only, ordered by sort_order


def test_list_excludes_inactive(client, db_session):
    make_expense_category(db_session, owner_id=OWNER_A, name="Active", sort_order=1)
    make_expense_category(
        db_session, owner_id=OWNER_A, name="Inactive", sort_order=2, is_active=False
    )
    body = client.get("/expense-categories").json()
    assert [c["name"] for c in body] == ["Active"]


def test_predefined_categories_visible_to_all_owners(client_factory, db_session):
    make_expense_category(db_session, owner_id=None, key="tax", name="Tax", sort_order=1)
    for owner in (OWNER_A, OWNER_B):
        c = client_factory(owner)
        body = c.get("/expense-categories").json()
        assert "Tax" in [cat["name"] for cat in body]

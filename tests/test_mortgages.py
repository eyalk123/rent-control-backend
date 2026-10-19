"""Mortgages on a property (layer 1): the monthly payment, its history, and the cash flow
after it — plus the two promises around them: a property without a mortgage is unchanged,
and the feature is off end to end outside Israel."""
from datetime import date, timedelta

import pytest
from dateutil.relativedelta import relativedelta

from app.models.property_mortgage import MortgagePayment, PropertyMortgage
from app.models.transaction import TransactionTypeEnum
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_expense_category, make_property, make_renter, make_transaction

TODAY = date.today()


def _url(prop_id: int) -> str:
    return f"/properties/{prop_id}/mortgages"


def _rented(db_session, rent=7000.0, **kw):
    prop = make_property(db_session, **kw)
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=TODAY - relativedelta(months=3),
        lease_years=[{"amount": rent, "type": "contract"}],
    )
    return prop


# --- the mortgage itself -------------------------------------------------------------------


def test_create_and_list(client, db_session):
    prop = make_property(db_session)
    body = client.post(
        _url(prop.id), json={"monthly_payment": 5358, "charge_day": 10, "end_date": "2049-10-10"}
    )
    assert body.status_code == 201, body.text
    m = body.json()
    assert m["charge_day"] == 10
    assert m["end_date"] == "2049-10-10"
    assert m["current_payment"] == 5358
    # No date given: the payment applies from today.
    assert [p["effective_from"] for p in m["payments"]] == [TODAY.isoformat()]

    assert [x["id"] for x in client.get(_url(prop.id)).json()] == [m["id"]]


def test_validation(client, db_session):
    url = _url(make_property(db_session).id)
    assert client.post(url, json={"monthly_payment": 0, "charge_day": 10}).status_code == 422
    assert client.post(url, json={"monthly_payment": 100, "charge_day": 0}).status_code == 422
    assert client.post(url, json={"monthly_payment": 100, "charge_day": 32}).status_code == 422
    assert client.post(url, json={"charge_day": 10}).status_code == 422


def test_new_payment_keeps_history_and_the_current_one_is_latest_started(client, db_session):
    prop = make_property(db_session)
    m = client.post(
        _url(prop.id),
        json={"monthly_payment": 5000, "payment_from": "2024-01-01", "charge_day": 10},
    ).json()
    pay_url = f"{_url(prop.id)}/{m['id']}/payments"

    # A rate change: the new amount from last month on.
    raised = client.post(
        pay_url, json={"amount": 5358, "effective_from": (TODAY - timedelta(days=30)).isoformat()}
    ).json()
    assert raised["current_payment"] == 5358
    assert [p["amount"] for p in raised["payments"]] == [5358, 5000]

    # Announced by the bank for later: recorded, not yet current.
    later = client.post(
        pay_url, json={"amount": 6000, "effective_from": (TODAY + timedelta(days=40)).isoformat()}
    ).json()
    assert later["current_payment"] == 5358
    assert [p["amount"] for p in later["payments"]] == [6000, 5358, 5000]


def test_delete_payment_but_never_the_last(client, db_session):
    prop = make_property(db_session)
    m = client.post(
        _url(prop.id),
        json={"monthly_payment": 5000, "payment_from": "2024-01-01", "charge_day": 10},
    ).json()
    pay_url = f"{_url(prop.id)}/{m['id']}/payments"
    newer = client.post(pay_url, json={"amount": 5358, "effective_from": "2025-01-01"}).json()
    newest_id = newer["payments"][0]["id"]

    after = client.delete(f"{pay_url}/{newest_id}")
    assert after.status_code == 200
    assert after.json()["current_payment"] == 5000
    assert client.delete(f"{pay_url}/{newest_id}").status_code == 404

    only = after.json()["payments"][0]["id"]
    assert client.delete(f"{pay_url}/{only}").status_code == 400


def test_update_and_clear_end_date(client, db_session):
    prop = make_property(db_session)
    m = client.post(
        _url(prop.id), json={"monthly_payment": 5000, "charge_day": 10, "end_date": "2049-10-10"}
    ).json()
    url = f"{_url(prop.id)}/{m['id']}"
    body = client.patch(url, json={"charge_day": 15}).json()
    assert body["charge_day"] == 15
    assert body["end_date"] == "2049-10-10"  # untouched when not sent
    assert client.patch(url, json={"end_date": None}).json()["end_date"] is None


def test_ended_mortgage_has_no_current_payment(client, db_session):
    prop = make_property(db_session)
    m = client.post(
        _url(prop.id),
        json={
            "monthly_payment": 5000,
            "payment_from": "2020-01-01",
            "charge_day": 10,
            "end_date": (TODAY - timedelta(days=1)).isoformat(),
        },
    ).json()
    assert m["current_payment"] is None


def test_owner_scoped(client_factory, db_session):
    prop = make_property(db_session, owner_id=OWNER_A)
    m = client_factory(OWNER_A).post(
        _url(prop.id), json={"monthly_payment": 5000, "charge_day": 10}
    ).json()

    b = client_factory(OWNER_B)
    assert b.get(_url(prop.id)).status_code == 404
    assert b.post(_url(prop.id), json={"monthly_payment": 1, "charge_day": 1}).status_code == 404
    assert b.patch(f"{_url(prop.id)}/{m['id']}", json={"charge_day": 2}).status_code == 404
    assert b.delete(f"{_url(prop.id)}/{m['id']}").status_code == 404


def test_one_mortgage_per_property(client, db_session):
    prop = make_property(db_session)
    assert client.post(_url(prop.id), json={"monthly_payment": 5000, "charge_day": 10}).status_code == 201
    assert client.post(_url(prop.id), json={"monthly_payment": 2000, "charge_day": 1}).status_code == 409


def test_deleting_mortgage_or_property_removes_everything(client, db_session):
    prop = make_property(db_session)
    first = client.post(_url(prop.id), json={"monthly_payment": 5000, "charge_day": 10}).json()
    assert client.delete(f"{_url(prop.id)}/{first['id']}").status_code == 204
    assert client.get(_url(prop.id)).json() == []
    assert db_session.query(MortgagePayment).count() == 0

    client.post(_url(prop.id), json={"monthly_payment": 2000, "charge_day": 1})
    assert client.delete(f"/properties/{prop.id}").status_code == 204
    assert db_session.query(PropertyMortgage).count() == 0
    assert db_session.query(MortgagePayment).count() == 0


# --- the property form's switch ------------------------------------------------------------

_FORM = {"address": "1 Herzl St", "city": "Tel Aviv", "type": "apartment"}


def test_form_creates_property_with_mortgage(client):
    body = client.post(
        "/properties",
        json={**_FORM, "mortgage": {"monthly_payment": 5358, "charge_day": 10, "end_date": "2049-10-10"}},
    )
    assert body.status_code == 201, body.text
    m = body.json()["mortgage"]
    assert m["current_payment"] == 5358
    assert m["charge_day"] == 10
    assert m["end_date"] == "2049-10-10"
    assert [p["effective_from"] for p in m["payments"]] == [TODAY.isoformat()]


def test_prime_track_defaults_on_and_follows_the_form(client):
    """On unless the form turns it off; an edit sets it either way."""
    prop = client.post(
        "/properties", json={**_FORM, "mortgage": {"monthly_payment": 5000, "charge_day": 10}}
    ).json()
    assert prop["mortgage"]["has_prime_track"] is True
    form = {"monthly_payment": 5000, "charge_day": 10}
    url = f"/properties/{prop['id']}"
    off = client.patch(url, json={"mortgage": {**form, "has_prime_track": False}}).json()
    assert off["mortgage"]["has_prime_track"] is False
    on = client.patch(url, json={"mortgage": {**form, "has_prime_track": True}}).json()
    assert on["mortgage"]["has_prime_track"] is True
    # A save that leaves the mortgage out leaves the switch too.
    client.patch(url, json={"mortgage": {**form, "has_prime_track": False}})
    assert client.patch(url, json={"city": "Haifa"}).json()["mortgage"]["has_prime_track"] is False


def test_property_without_mortgage_reads_null(client):
    assert client.post("/properties", json=_FORM).json()["mortgage"] is None


def test_form_edit_corrects_todays_amount_without_adding_history(client):
    prop = client.post(
        "/properties", json={**_FORM, "mortgage": {"monthly_payment": 5385, "charge_day": 10}}
    ).json()
    body = client.patch(
        f"/properties/{prop['id']}",
        json={"mortgage": {"monthly_payment": 5358, "charge_day": 12, "end_date": None}},
    ).json()
    m = body["mortgage"]
    assert m["current_payment"] == 5358
    assert m["charge_day"] == 12
    assert [p["amount"] for p in m["payments"]] == [5358]  # corrected, not appended


def test_form_edit_corrects_the_entry_in_force_not_a_future_one(client):
    prop = client.post(
        "/properties",
        json={**_FORM, "mortgage": {"monthly_payment": 5000, "payment_from": "2024-01-01", "charge_day": 10}},
    ).json()
    m = prop["mortgage"]
    client.post(
        f"{_url(prop['id'])}/{m['id']}/payments",
        json={"amount": 6000, "effective_from": (TODAY + timedelta(days=40)).isoformat()},
    )
    body = client.patch(
        f"/properties/{prop['id']}", json={"mortgage": {"monthly_payment": 5100, "charge_day": 10}}
    ).json()
    assert [p["amount"] for p in body["mortgage"]["payments"]] == [6000, 5100]


def test_form_switch_off_deletes_mortgage_and_history(client, db_session):
    prop = client.post(
        "/properties", json={**_FORM, "mortgage": {"monthly_payment": 5000, "charge_day": 10}}
    ).json()
    body = client.patch(f"/properties/{prop['id']}", json={"mortgage": None}).json()
    assert body["mortgage"] is None
    assert db_session.query(PropertyMortgage).count() == 0
    assert db_session.query(MortgagePayment).count() == 0


def test_form_save_without_mortgage_key_leaves_it(client):
    """Older mobile builds never send `mortgage`; their saves must not delete it."""
    prop = client.post(
        "/properties", json={**_FORM, "mortgage": {"monthly_payment": 5000, "charge_day": 10}}
    ).json()
    body = client.patch(f"/properties/{prop['id']}", json={"city": "Haifa"}).json()
    assert body["mortgage"]["current_payment"] == 5000


def test_form_mortgage_refused_outside_israel(client, db_session):
    prop = make_property(db_session, country="GB")
    r = client.patch(f"/properties/{prop.id}", json={"mortgage": {"monthly_payment": 1, "charge_day": 1}})
    assert r.status_code == 422


# --- the cash flow after it ----------------------------------------------------------------


def test_cash_flow_on_the_property(client, db_session):
    prop = _rented(db_session, rent=7000)
    # 12-month expenses of 6,000 → 500 a month.
    make_transaction(
        db_session, property_id=prop.id, amount=6000, type=TransactionTypeEnum.EXPENSE,
        date_of_payment=TODAY - timedelta(days=10),
    )
    client.post(_url(prop.id), json={"monthly_payment": 5358, "charge_day": 10})

    r = client.get(f"/properties/{prop.id}").json()["returns"]
    assert r["monthly_mortgage"] == 5358
    assert r["monthly_expenses"] == pytest.approx(500)
    assert r["monthly_left"] == pytest.approx(7000 - 5358 - 500)
    assert r["mortgage_coverage"] == pytest.approx(7000 / 5358)  # 131%

    listed = next(p for p in client.get("/properties").json() if p["id"] == prop.id)
    assert listed["returns"] == r


def test_property_without_mortgage_is_unchanged(client, db_session):
    """Most properties have no mortgage: no figure, and no "missing" reason either."""
    prop = _rented(db_session)
    r = client.get(f"/properties/{prop.id}").json()["returns"]
    for name in ("monthly_mortgage", "monthly_expenses", "monthly_left", "mortgage_coverage"):
        assert r[name] is None
        assert name not in r["unavailable"]


def test_vacant_property_withholds_left_and_coverage(client, db_session):
    prop = make_property(db_session)
    client.post(_url(prop.id), json={"monthly_payment": 5000, "charge_day": 10})
    r = client.get(f"/properties/{prop.id}").json()["returns"]
    assert r["monthly_mortgage"] == 5000
    assert r["monthly_left"] is None
    assert r["mortgage_coverage"] is None
    assert r["unavailable"]["monthly_left"] == "vacant"


def test_paid_off_mortgage_counts_nothing(client, db_session):
    prop = _rented(db_session, rent=7000)
    client.post(
        _url(prop.id),
        json={
            "monthly_payment": 5000,
            "payment_from": "2020-01-01",
            "charge_day": 10,
            "end_date": (TODAY - timedelta(days=1)).isoformat(),
        },
    )
    r = client.get(f"/properties/{prop.id}").json()["returns"]
    assert r["monthly_mortgage"] == 0
    assert r["monthly_left"] == pytest.approx(7000)
    assert r["unavailable"]["mortgage_coverage"] == "no_active_mortgage"


# --- Israel only ---------------------------------------------------------------------------


def test_off_outside_israel_end_to_end(client, db_session):
    prop = _rented(db_session, country="GB")
    db_session.add(PropertyMortgage(property_id=prop.id, charge_day=1))
    db_session.commit()

    assert client.get(_url(prop.id)).status_code == 422
    assert client.post(_url(prop.id), json={"monthly_payment": 1, "charge_day": 1}).status_code == 422
    r = client.get(f"/properties/{prop.id}").json()["returns"]
    assert r["monthly_mortgage"] is None


def test_mortgage_interest_category_hidden_in_israel_only(client, db_session):
    from app.models.owner import Owner

    make_expense_category(db_session, owner_id=None, key="repairs", name="Repairs", sort_order=1)
    make_expense_category(
        db_session, owner_id=None, key="mortgage_interest", name="Mortgage interest", sort_order=2
    )

    # No country resolves to Israel, like every account predating the country column.
    assert [c["key"] for c in client.get("/expense-categories").json()] == ["repairs"]

    owner = db_session.get(Owner, OWNER_A)
    if owner is None:
        owner = Owner(id=OWNER_A, email="a@b.c")
        db_session.add(owner)
    owner.country = "GB"
    db_session.commit()
    assert [c["key"] for c in client.get("/expense-categories").json()] == [
        "repairs",
        "mortgage_interest",
    ]


# --- the bank's yearly summary (layer 2) ----------------------------------------------------


def _mortgage_id(client, prop_id):
    return client.post(_url(prop_id), json={"monthly_payment": 5358, "charge_day": 10}).json()["id"]


def test_a_year_is_saved_replaced_and_deleted(client, db_session):
    prop = make_property(db_session)
    mid = _mortgage_id(client, prop.id)
    year = TODAY.year - 1
    url = f"{_url(prop.id)}/{mid}/years/{year}"

    m = client.put(url, json={"interest": 41049, "linkage": 8235, "balance": 984720}).json()
    assert m["years"] == [
        {**m["years"][0], "year": year, "interest": 41049, "linkage": 8235, "balance": 984720,
         "document": None}
    ]
    m = client.put(url, json={"interest": 40000, "linkage": 0}).json()
    assert [(y["interest"], y["balance"]) for y in m["years"]] == [(40000, None)]
    # Newest year first.
    client.put(f"{_url(prop.id)}/{mid}/years/{year - 1}", json={"interest": 1, "linkage": 0})
    assert [y["year"] for y in client.get(_url(prop.id)).json()[0]["years"]] == [year, year - 1]

    assert client.delete(url).status_code == 200
    assert client.delete(url).status_code == 404


def test_only_a_year_that_is_over(client, db_session):
    prop = make_property(db_session)
    mid = _mortgage_id(client, prop.id)
    body = {"interest": 1, "linkage": 0}
    assert client.put(f"{_url(prop.id)}/{mid}/years/{TODAY.year}", json=body).status_code == 422
    assert client.put(f"{_url(prop.id)}/{mid}/years/{TODAY.year - 1}", json={"interest": -1, "linkage": 0}).status_code == 422
    assert client.put(f"{_url(prop.id)}/{mid}/years/{TODAY.year - 1}", json={"interest": 1}).status_code == 422


def test_the_statement_is_kept_as_a_property_file(client, db_session, monkeypatch):
    released = []
    monkeypatch.setattr(
        "app.services.mortgage_service.release_file_urls",
        lambda db, owner, urls: released.extend(urls),
    )
    prop = make_property(db_session)
    mid = _mortgage_id(client, prop.id)
    url = f"{_url(prop.id)}/{mid}/years/{TODAY.year - 1}"
    first = f"properties/{OWNER_A}/u1/leumi.pdf"

    m = client.put(url, json={"interest": 1, "linkage": 0, "document_url": first,
                               "document_label": "Mortgage statement"}).json()
    assert m["years"][0]["document"]["url"] == first
    assert [f["label"] for f in client.get(f"/properties/{prop.id}/files").json()] == ["Mortgage statement"]

    # Saving without the field keeps it; a new one replaces it and releases the old file.
    client.put(url, json={"interest": 2, "linkage": 0})
    assert client.get(_url(prop.id)).json()[0]["years"][0]["document"]["url"] == first
    second = f"properties/{OWNER_A}/u2/leumi.pdf"
    client.put(url, json={"interest": 2, "linkage": 0, "document_url": second})
    assert [f["url"] for f in client.get(f"/properties/{prop.id}/files").json()] == [second]
    assert released == [first]

    client.delete(url)
    assert client.get(f"/properties/{prop.id}/files").json() == []
    assert released == [first, second]


def test_years_are_owner_scoped(client_factory, db_session):
    prop = make_property(db_session)
    mid = _mortgage_id(client_factory(OWNER_A), prop.id)
    other = client_factory(OWNER_B)
    resp = other.put(f"{_url(prop.id)}/{mid}/years/{TODAY.year - 1}", json={"interest": 1, "linkage": 0})
    assert resp.status_code == 404

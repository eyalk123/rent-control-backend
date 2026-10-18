"""Full-stack tests for the owner setting a CPI year's rent by hand: the
`PUT /renters/{id}/lease-years/{n}/amount` endpoint, and the flag surviving lease edits
and the nightly indexing job."""
import json
from datetime import date

from dateutil.relativedelta import relativedelta

from app.config import settings
from app.repositories.cpi_index_repository import reference_period
from tests.factories import make_renter
from tests.test_cpi_indexing import (
    CRON_SECRET,
    FakeSource,
    _cpi_notifications,
    _post,
    _seed_readings,
)


def _confirmed_renter(client, db_session, monkeypatch):
    """A CPI lease whose year 2 has just been confirmed at 5500 (index 100 -> 110), ten
    days before it starts — the moment the owner sees the confirmed alert."""
    today = date.today()
    anniversary = today + relativedelta(days=10)
    lease_start = anniversary - relativedelta(years=1)
    _seed_readings(
        db_session,
        {
            reference_period(lease_start): 100.0,
            reference_period(today): 110.0,
            reference_period(anniversary): 110.0,
        },
    )
    renter = make_renter(
        db_session,
        rent_escalation_mode="cpi",
        lease_start=lease_start,
        lease_end=lease_start + relativedelta(years=3),
        base_rent=5000.0,
        cpi_base_index=100.0,
        lease_years=[{"amount": 5000.0, "type": "contract"} for _ in range(3)],
    )
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CRON_SECRET)
    assert _post(client, [FakeSource("cbs"), FakeSource("boi")]).json()["notified"] == 1
    return renter, anniversary


def _set(client, renter_id, year_index, amount):
    return client.put(
        f"/renters/{renter_id}/lease-years/{year_index}/amount", json={"amount": amount}
    )


def test_setting_the_amount_stores_it_as_typed_and_rebases_later_years(
    client, db_session, monkeypatch
):
    renter, _ = _confirmed_renter(client, db_session, monkeypatch)

    resp = _set(client, renter.id, 1, 5450)

    assert resp.status_code == 200
    years = resp.json()["lease_years"]
    assert years[1] == {"amount": 5450, "type": "contract", "set_by_owner": True}
    # Year 3 has no newer reading yet, so it projects flat — off 5450, not 5500.
    assert years[2]["amount"] == 5450
    assert "set_by_owner" not in years[2]


def test_setting_the_amount_dismisses_that_years_alert(client, db_session, monkeypatch):
    renter, anniversary = _confirmed_renter(client, db_session, monkeypatch)
    assert [r.period_key for r in _cpi_notifications(db_session)] == [anniversary.isoformat()]

    assert _set(client, renter.id, 1, 5450).status_code == 200

    # The listing leaves dismissed rows out.
    assert _cpi_notifications(db_session) == []


def test_the_nightly_job_leaves_an_owner_set_year_alone(client, db_session, monkeypatch):
    renter, _ = _confirmed_renter(client, db_session, monkeypatch)
    assert _set(client, renter.id, 1, 5450).status_code == 200

    _post(client, [FakeSource("cbs"), FakeSource("boi")])

    db_session.refresh(renter)
    years = json.loads(renter.lease_years)
    assert years[1]["amount"] == 5450
    assert years[1]["set_by_owner"] is True


def test_a_year_whose_index_is_not_in_yet_cannot_be_set(client, db_session, monkeypatch):
    renter, _ = _confirmed_renter(client, db_session, monkeypatch)

    # Year 3's own index month is a year away: its amount is still a projection.
    resp = _set(client, renter.id, 2, 5600)

    assert resp.status_code == 409


def test_an_owner_set_year_can_be_set_again(client, db_session, monkeypatch):
    renter, _ = _confirmed_renter(client, db_session, monkeypatch)
    assert _set(client, renter.id, 1, 5450).status_code == 200

    resp = _set(client, renter.id, 1, 5480)

    assert resp.status_code == 200
    assert resp.json()["lease_years"][1]["amount"] == 5480


def test_year_one_out_of_range_and_bad_amounts_are_rejected(client, db_session, monkeypatch):
    renter, _ = _confirmed_renter(client, db_session, monkeypatch)

    assert _set(client, renter.id, 0, 5000).status_code == 422
    assert _set(client, renter.id, 3, 5000).status_code == 422
    assert _set(client, renter.id, 1, 0).status_code == 422
    assert _set(client, renter.id, 1, -10).status_code == 422


def test_a_lease_without_index_linkage_is_rejected(client, db_session):
    renter = make_renter(
        db_session,
        rent_escalation_mode="percent",
        lease_start=date(2025, 1, 1),
        lease_years=[
            {"amount": 5000.0, "type": "contract"},
            {"amount": 5250.0, "type": "contract"},
        ],
    )

    assert _set(client, renter.id, 1, 5300).status_code == 409


def test_another_owners_renter_is_forbidden(client_factory, db_session, monkeypatch):
    from tests.conftest import OWNER_B

    client = client_factory()
    renter, _ = _confirmed_renter(client, db_session, monkeypatch)

    resp = _set(client_factory(OWNER_B), renter.id, 1, 5450)

    assert resp.status_code == 403


def _lease_years_payload(renter):
    """What a client form sends back: the years as it read them, minus nothing — a
    client that echoes the flag must not be the reason it survives."""
    return [
        {"amount": y["amount"], "type": y["type"]} for y in json.loads(renter.lease_years)
    ]


def test_a_lease_edit_keeps_the_owner_amount(client, db_session, monkeypatch):
    renter, _ = _confirmed_renter(client, db_session, monkeypatch)
    assert _set(client, renter.id, 1, 5450).status_code == 200
    db_session.refresh(renter)
    payload = _lease_years_payload(renter)
    payload[1]["amount"] = 5500  # a stale form value

    resp = client.patch(f"/renters/{renter.id}", json={"lease_years": payload})

    assert resp.status_code == 200
    year = resp.json()["lease_years"][1]
    assert year["amount"] == 5450
    assert year["set_by_owner"] is True


def test_moving_the_lease_start_keeps_the_owner_amount(client, db_session, monkeypatch):
    renter, _ = _confirmed_renter(client, db_session, monkeypatch)
    assert _set(client, renter.id, 1, 5450).status_code == 200
    db_session.refresh(renter)

    resp = client.patch(
        f"/renters/{renter.id}",
        json={
            "lease_start": (renter.lease_start - relativedelta(days=3)).isoformat(),
            "lease_years": _lease_years_payload(renter),
        },
    )

    assert resp.status_code == 200
    assert resp.json()["lease_years"][1]["amount"] == 5450
    assert resp.json()["lease_years"][1]["set_by_owner"] is True


def test_shortening_the_lease_drops_the_owner_amount_with_its_year(
    client, db_session, monkeypatch
):
    renter, _ = _confirmed_renter(client, db_session, monkeypatch)
    assert _set(client, renter.id, 1, 5450).status_code == 200
    db_session.refresh(renter)

    resp = client.patch(
        f"/renters/{renter.id}", json={"lease_years": _lease_years_payload(renter)[:1]}
    )

    assert resp.status_code == 200
    assert len(resp.json()["lease_years"]) == 1


def test_leaving_index_linkage_drops_the_flag(client, db_session, monkeypatch):
    renter, _ = _confirmed_renter(client, db_session, monkeypatch)
    assert _set(client, renter.id, 1, 5450).status_code == 200
    db_session.refresh(renter)

    resp = client.patch(
        f"/renters/{renter.id}",
        json={"rent_escalation_mode": "none", "lease_years": _lease_years_payload(renter)},
    )

    assert resp.status_code == 200
    assert all("set_by_owner" not in y for y in resp.json()["lease_years"])


def test_a_client_cannot_set_the_flag(client, db_session, monkeypatch):
    renter, _ = _confirmed_renter(client, db_session, monkeypatch)
    payload = _lease_years_payload(renter)
    payload[2]["set_by_owner"] = True
    payload[2]["amount"] = 1

    resp = client.patch(f"/renters/{renter.id}", json={"lease_years": payload})

    assert resp.status_code == 200
    year = resp.json()["lease_years"][2]
    assert "set_by_owner" not in year
    assert year["amount"] != 1

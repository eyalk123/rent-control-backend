"""A locked property is inaccessible, not read-only.

On the free plan (two properties) the third property here is locked. Everything about it
is closed — detail, renters, transactions, documents, reports, reminders, CPI repricing,
the assistant — with two deliberate exceptions: the properties list still shows it as a
stub, and the account export still includes it. Deleting it stays allowed, because that
is how an account gets back under its limit.

All of it is behind ENTITLEMENT_ENFORCED; the last test pins that nothing changes while
it is off.
"""
import io
import json
import zipfile
from datetime import date, datetime, timedelta

import pytest
from freezegun import freeze_time
from openpyxl import load_workbook

from app.config import settings
from app.models.owner import Owner
from app.models.transaction import TransactionTypeEnum
from app.repositories.notification_repository import NotificationRepository
from app.services.agent_tools import AgentTools
from tests.conftest import OWNER_A
from tests.factories import make_property, make_renter, make_transaction
from tests.test_cpi_indexing import (
    CRON_SECRET as CPI_SECRET,
    FakeSource,
    _fresh_period,
    _post as _post_cpi,
    _seed_index,
)

YEAR = 2026


@pytest.fixture
def enforced():
    original = settings.ENTITLEMENT_ENFORCED
    settings.ENTITLEMENT_ENFORCED = True
    yield
    settings.ENTITLEMENT_ENFORCED = original


@pytest.fixture
def portfolio(db_session):
    """Three properties on the free plan: the newest one is locked.

    Each property has a renter and a revenue transaction, so every read path has
    something of the locked property's to leave out.
    """
    db_session.add(Owner(id=OWNER_A, email="a@b.c"))
    db_session.commit()
    base = datetime(2026, 1, 1, 12, 0, 0)
    props, renters, txns = [], [], []
    for i in range(3):
        prop = make_property(
            db_session,
            address=f"{i + 1} Lock St",
            created_at=base + timedelta(days=i),
        )
        renter = make_renter(db_session, property_id=prop.id, first_name=f"Tenant{i + 1}")
        txn = make_transaction(
            db_session,
            property_id=prop.id,
            renter_id=renter.id,
            amount=1000.0 * (i + 1),
            date_of_payment=date(YEAR, 3, 1),
            month_for=date(YEAR, 3, 1),
            property_address=f"{i + 1} Lock St",
        )
        props.append(prop)
        renters.append(renter)
        txns.append(txn)
    return props, renters, txns


# ── The stub, and the 402s ───────────────────────────────────────────────────


def test_the_list_shows_a_locked_property_only_as_a_stub(client, portfolio, enforced):
    props, _, _ = portfolio
    listed = {p["id"]: p for p in client.get("/properties").json()}

    open_one = listed[props[0].id]
    assert open_one["locked"] is False
    assert open_one["purchase_price"] == 1_000_000.0

    stub = listed[props[2].id]
    assert stub["locked"] is True
    assert stub["address"] == "3 Lock St"
    assert stub["city"] == "Tel Aviv"
    # Nothing beyond what identifies it.
    assert stub["purchase_price"] is None
    assert stub["zip_code"] is None
    assert stub["renters"] is None


@pytest.mark.parametrize(
    "path",
    [
        "/properties/{pid}",
        "/properties/{pid}/renters",
        "/properties/{pid}/files",
        "/renters/{rid}",
        "/transactions/{tid}",
    ],
)
def test_every_read_of_a_locked_property_is_a_402(client, portfolio, enforced, path):
    props, renters, txns = portfolio
    url = path.format(pid=props[2].id, rid=renters[2].id, tid=txns[2].id)

    response = client.get(url)

    assert response.status_code == 402
    assert response.json()["detail"]["error"] == "property_locked"
    assert response.json()["detail"]["property_id"] == props[2].id


def test_the_same_reads_of_an_open_property_still_work(client, portfolio, enforced):
    props, renters, txns = portfolio
    assert client.get(f"/properties/{props[0].id}").status_code == 200
    assert client.get(f"/renters/{renters[0].id}").status_code == 200
    assert client.get(f"/transactions/{txns[0].id}").status_code == 200


def test_writes_that_used_to_slip_through_are_refused(client, portfolio, enforced):
    """Document uploads, transaction deletes and renter deletes were never gated."""
    props, renters, txns = portfolio

    upload = client.post(
        f"/properties/{props[2].id}/files/bulk",
        json=[{"url": "https://x/y.pdf", "label": "y.pdf"}],
    )
    assert upload.status_code == 402
    assert client.delete(f"/transactions/{txns[2].id}").status_code == 402
    assert client.delete(f"/renters/{renters[2].id}").status_code == 402


def test_a_foreign_property_is_still_a_404_not_a_402(client_factory, portfolio, enforced):
    props, _, _ = portfolio
    other = client_factory("owner-b")
    assert other.get(f"/properties/{props[2].id}").status_code == 404


def test_deleting_a_locked_property_is_allowed(client, portfolio, enforced):
    props, _, _ = portfolio
    assert client.delete(f"/properties/{props[2].id}").status_code == 204


# ── Left out of every list and total ─────────────────────────────────────────


def test_lists_leave_the_locked_property_out(client, portfolio, enforced):
    props, renters, txns = portfolio

    renter_ids = {r["id"] for r in client.get("/renters").json()}
    assert renters[2].id not in renter_ids
    assert renters[0].id in renter_ids

    txn_ids = {t["id"] for t in client.get("/transactions").json()}
    assert txns[2].id not in txn_ids
    assert txns[0].id in txn_ids


@freeze_time(f"{YEAR}-03-20")
def test_the_dashboard_summary_leaves_the_locked_property_out(client, portfolio, enforced):
    body = client.get("/transactions/summary").json()
    march = next(b for b in body["six_month_buckets"] if b["key"] == f"{YEAR}-03")
    # 1000 + 2000; the locked property's 3000 is not counted.
    assert march["revenue"] == 3000.0
    assert body["ytd_net"] == 3000.0


@freeze_time(f"{YEAR}-03-20")
def test_overdue_and_expiring_leave_the_locked_property_out(
    client, db_session, portfolio, enforced
):
    props, _, _ = portfolio
    for prop in (props[0], props[2]):
        make_renter(
            db_session,
            property_id=prop.id,
            first_name="Late",
            lease_start=date(YEAR - 1, 12, 1),
            lease_end=date(YEAR, 4, 15),
            payment_day_of_month=1,
        )
    overdue = {r["property_id"] for r in client.get("/renters/overdue").json()}
    expiring = {r["property_id"] for r in client.get("/renters/expiring").json()}

    assert props[0].id in overdue and props[2].id not in overdue
    assert props[0].id in expiring and props[2].id not in expiring


def test_reports_leave_the_locked_property_out(client, portfolio, enforced):
    income = client.get(
        "/reports/income-expense", params={"year": YEAR, "format": "csv"}
    ).content.decode("utf-8-sig")
    assert "1 Lock St" in income
    assert "3 Lock St" not in income


def test_the_expense_log_leaves_the_locked_property_out(
    client, db_session, portfolio, enforced
):
    props, _, _ = portfolio
    for prop in (props[0], props[2]):
        make_transaction(
            db_session,
            type=TransactionTypeEnum.EXPENSE,
            property_id=prop.id,
            amount=100.0,
            date_of_payment=date(YEAR, 4, 1),
            notes=f"expense-{prop.address}",
        )
    log = client.get(
        "/reports/expense-log", params={"year": YEAR, "format": "csv"}
    ).content.decode("utf-8-sig")
    assert "expense-1 Lock St" in log
    assert "expense-3 Lock St" not in log


def test_the_assistant_does_not_see_the_locked_property(db_session, portfolio, enforced):
    props, renters, _ = portfolio
    tools = AgentTools(db_session)

    listed = json.dumps(tools.dispatch("list_properties", OWNER_A, {}))
    assert "1 Lock St" in listed
    assert "3 Lock St" not in listed

    direct = tools.dispatch("get_property", OWNER_A, {"property_id": props[2].id})
    assert direct == {"error": "not found"}


# ── Reminders and CPI ────────────────────────────────────────────────────────


@freeze_time(f"{YEAR}-03-20")
def test_no_reminder_is_created_or_listed_for_the_locked_property(
    client, db_session, portfolio, enforced
):
    props, _, _ = portfolio
    locked_late = make_renter(
        db_session,
        property_id=props[2].id,
        first_name="Late",
        lease_start=date(YEAR - 1, 12, 1),
        lease_end=date(YEAR + 1, 12, 1),
        payment_day_of_month=1,
    )

    feed = client.get("/notifications").json()

    assert all(n["renter_id"] != locked_late.id for n in feed)
    stored = NotificationRepository(db_session).list_for_owner(OWNER_A)
    assert all(n.entity_id != locked_late.id for n in stored)


def test_cpi_indexing_skips_a_locked_property(
    client, db_session, portfolio, enforced, monkeypatch
):
    props, _, _ = portfolio
    _seed_index(db_session, [(2022, 11, 100.0), (2023, 11, 110.0), _fresh_period()])
    stale = [
        {"amount": 5000.0, "type": "contract"},
        {"amount": 5000.0, "type": "contract"},
    ]
    kw = dict(
        rent_escalation_mode="cpi",
        lease_start=date(2023, 1, 1),
        base_rent=5000.0,
        cpi_base_index=100.0,
        lease_years=stale,
    )
    open_renter = make_renter(db_session, property_id=props[0].id, **kw)
    locked_renter = make_renter(db_session, property_id=props[2].id, **kw)
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", CPI_SECRET)

    assert _post_cpi(client, [FakeSource("cbs"), FakeSource("boi")]).status_code == 200

    db_session.refresh(open_renter)
    db_session.refresh(locked_renter)
    assert [y["amount"] for y in json.loads(open_renter.lease_years)] == [5000, 5500]
    assert [y["amount"] for y in json.loads(locked_renter.lease_years)] == [5000, 5000]


# ── The exceptions, and the switch ───────────────────────────────────────────


def test_the_export_still_includes_the_locked_property(
    client, portfolio, enforced, monkeypatch
):
    monkeypatch.setattr(
        "app.services.firebase_storage.list_owner_blobs", lambda owner_id: []
    )
    response = client.get("/users/me/export")

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        wb = load_workbook(io.BytesIO(archive.read("rentvance-data.xlsx")))
        addresses = [row[1] for row in wb["Properties"].values][1:]
    assert "3 Lock St" in addresses


def test_nothing_is_hidden_while_enforcement_is_off(client, portfolio):
    props, renters, txns = portfolio
    assert settings.ENTITLEMENT_ENFORCED is False

    listed = {p["id"]: p for p in client.get("/properties").json()}
    assert listed[props[2].id]["purchase_price"] == 1_000_000.0
    assert client.get(f"/properties/{props[2].id}").status_code == 200
    assert client.get(f"/renters/{renters[2].id}").status_code == 200
    assert client.get(f"/transactions/{txns[2].id}").status_code == 200
    assert txns[2].id in {t["id"] for t in client.get("/transactions").json()}

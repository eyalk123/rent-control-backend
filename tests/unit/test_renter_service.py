"""Unit tests for RenterService business logic (real repos over the test session)."""
import json
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from freezegun import freeze_time

from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.schemas.renter import ExtraContact, LeaseYear, RenterCreate, RenterUpdate
from app.services.renter_service import (
    RenterService,
    _compute_lease_end,
    _payment_interval_months,
)
from tests.conftest import OWNER_A
from tests.factories import make_property, make_renter, make_transaction
from app.models.transaction import TransactionTypeEnum


def _service(db_session):
    return RenterService(RenterRepository(db_session), PropertyRepository(db_session))


def test_compute_lease_end_adds_years():
    assert _compute_lease_end(date(2025, 1, 1), 3) == date(2028, 1, 1)


def test_create_renter_encodes_lease_years_and_computes_end(db_session):
    svc = _service(db_session)
    data = RenterCreate(
        first_name="A",
        last_name="B",
        phone="1",
        email="a@b.c",
        lease_start=date(2025, 1, 1),
        lease_years=[
            LeaseYear(amount=12000, type="contract"),
            LeaseYear(amount=13000, type="option"),
        ],
        extra_contacts=[ExtraContact(name="Mom", phone="999")],
    )
    renter = svc.create_renter(data, OWNER_A)

    # lease_years persisted as JSON text
    assert json.loads(renter.lease_years)[0]["amount"] == 12000
    # lease_end = start + 2 years
    assert renter.lease_end == date(2027, 1, 1)
    # extra_contacts stored as list of dicts
    assert renter.extra_contacts == [{"name": "Mom", "phone": "999"}]


def test_create_renter_no_lease_start_leaves_end_none(db_session):
    svc = _service(db_session)
    data = RenterCreate(
        first_name="A", last_name="B", phone="1", email="a@b.c",
        lease_years=[LeaseYear(amount=12000, type="contract")],
    )
    renter = svc.create_renter(data, OWNER_A)
    assert renter.lease_end is None


def test_create_renter_foreign_property_raises_403(db_session):
    svc = _service(db_session)
    foreign = make_property(db_session, owner_id="someone-else")
    data = RenterCreate(
        property_id=foreign.id,
        first_name="A", last_name="B", phone="1", email="a@b.c",
        lease_years=[LeaseYear(amount=1, type="contract")],
    )
    with pytest.raises(HTTPException) as exc:
        svc.create_renter(data, OWNER_A)
    assert exc.value.status_code == 403


def test_update_renter_recomputes_lease_end_on_new_lease_years(db_session):
    svc = _service(db_session)
    renter = make_renter(
        db_session,
        lease_start=date(2025, 1, 1),
        lease_end=date(2026, 1, 1),
        lease_years=[{"amount": 1, "type": "contract"}],
    )
    update = RenterUpdate(
        lease_years=[
            LeaseYear(amount=1, type="contract"),
            LeaseYear(amount=1, type="option"),
            LeaseYear(amount=1, type="option"),
        ]
    )
    updated = svc.update_renter(renter.id, update, OWNER_A)
    assert updated.lease_end == date(2028, 1, 1)  # start + 3 years


def test_get_renter_access_denied_for_other_owner(db_session):
    svc = _service(db_session)
    renter = make_renter(db_session, owner_id="other")
    with pytest.raises(HTTPException) as exc:
        svc.get_renter(renter.id, OWNER_A)
    assert exc.value.status_code == 403


@freeze_time("2026-02-20")
def test_overdue_excludes_future_payment_day(db_session):
    today = date(2026, 2, 20)
    svc = _service(db_session)
    prop = make_property(db_session)
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=today - timedelta(days=60),
        lease_end=today + timedelta(days=60),
        payment_day_of_month=28,  # 28 > 20 -> not yet due
        lease_years=[{"amount": 5000, "type": "contract"}],
    )
    assert svc.get_overdue_this_month(OWNER_A) == []


@freeze_time("2026-06-20")
def test_overdue_null_payment_day_falls_back_to_first(db_session):
    today = date(2026, 6, 20)
    svc = _service(db_session)
    prop = make_property(db_session)
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=today - timedelta(days=60),
        lease_end=today + timedelta(days=60),
        payment_day_of_month=None,  # no payment day -> treated as due on the 1st
        lease_years=[{"amount": 5000, "type": "contract"}],
    )
    [row] = svc.get_overdue_this_month(OWNER_A)
    assert row.days_overdue == today.day - 1  # overdue since the 1st
    assert row.payment_day_of_month is None  # fallback stays invisible


@freeze_time("2026-06-15")
def test_overdue_excludes_paid_renter(db_session):
    today = date(2026, 6, 15)
    svc = _service(db_session)
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        lease_start=today - timedelta(days=60),
        lease_end=today + timedelta(days=60),
        payment_day_of_month=1,
    )
    make_transaction(
        db_session,
        type=TransactionTypeEnum.REVENUE,
        property_id=prop.id,
        renter_id=renter.id,
        month_for=date(2026, 6, 1),
    )
    assert svc.get_overdue_this_month(OWNER_A) == []


@freeze_time("2026-02-20")
def test_overdue_skips_quarterly_off_month(db_session):
    # Quarterly cycle anchored on a Jan lease start -> due Jan/Apr/Jul/Oct.
    # February is an off-month, so no overdue alert despite an unpaid month.
    svc = _service(db_session)
    prop = make_property(db_session)
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=date(2026, 1, 5),
        lease_end=date(2027, 1, 5),
        payment_day_of_month=5,
        number_of_payments=4,  # quarterly
        lease_years=[{"amount": 5000, "type": "contract"}],
    )
    assert svc.get_overdue_this_month(OWNER_A) == []


@freeze_time("2026-04-20")
def test_overdue_quarterly_due_month_uses_period_amount(db_session):
    # April is +3 months from a Jan lease start -> a due month for a quarterly cycle.
    today = date(2026, 4, 20)
    svc = _service(db_session)
    prop = make_property(db_session)
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=date(2026, 1, 5),
        lease_end=date(2027, 1, 5),
        payment_day_of_month=5,
        number_of_payments=4,  # quarterly
        lease_years=[{"amount": 5000, "type": "contract"}],
    )
    [row] = svc.get_overdue_this_month(OWNER_A)
    assert row.days_overdue == today.day - 5  # overdue since the 5th
    assert row.monthly_amount == 5000 * 3  # per-period amount (3 months)


@freeze_time("2026-02-20")
def test_overdue_monthly_and_null_frequency_flagged_every_month(db_session):
    # Both an explicit monthly cadence and a null cadence alert every month.
    svc = _service(db_session)
    prop = make_property(db_session)
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=date(2026, 1, 5),
        lease_end=date(2027, 1, 5),
        payment_day_of_month=5,
        number_of_payments=12,  # monthly
        lease_years=[{"amount": 5000, "type": "contract"}],
    )
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=date(2026, 1, 5),
        lease_end=date(2027, 1, 5),
        payment_day_of_month=5,
        number_of_payments=None,  # unset -> treated as monthly
        lease_years=[{"amount": 4000, "type": "contract"}],
    )
    rows = svc.get_overdue_this_month(OWNER_A)
    assert len(rows) == 2
    assert {r.monthly_amount for r in rows} == {5000, 4000}  # interval 1, no multiplier


@pytest.mark.parametrize(
    "number_of_payments,expected_interval",
    [
        (12, 1),    # monthly
        (4, 3),     # quarterly
        (2, 6),     # semi-annual
        (1, 12),    # yearly
        (3, 4),     # every 4 months
        (0, 1),     # invalid -> monthly
        (-5, 1),    # invalid -> monthly
        (None, 1),  # unset -> monthly
    ],
)
def test_payment_interval_months_mapping(number_of_payments, expected_interval):
    assert _payment_interval_months(number_of_payments) == expected_interval


@freeze_time("2026-03-20")
def test_overdue_skips_yearly_off_month(db_session):
    # Yearly cadence anchored on a Jan lease start -> due only every January.
    # March is an off-month, so no overdue alert.
    svc = _service(db_session)
    prop = make_property(db_session)
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=date(2025, 1, 5),
        lease_end=date(2028, 1, 5),
        payment_day_of_month=5,
        number_of_payments=1,  # yearly
        lease_years=[{"amount": 5000, "type": "contract"}],
    )
    assert svc.get_overdue_this_month(OWNER_A) == []


@freeze_time("2026-01-20")
def test_overdue_yearly_due_month_uses_annual_amount(db_session):
    # January is +12 months from the Jan 2025 lease start -> a due month for a
    # yearly cycle; the period amount spans 12 months.
    today = date(2026, 1, 20)
    svc = _service(db_session)
    prop = make_property(db_session)
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=date(2025, 1, 5),
        lease_end=date(2028, 1, 5),
        payment_day_of_month=5,
        number_of_payments=1,  # yearly
        lease_years=[{"amount": 5000, "type": "contract"}],
    )
    [row] = svc.get_overdue_this_month(OWNER_A)
    assert row.days_overdue == today.day - 5  # overdue since the 5th
    assert row.monthly_amount == 5000 * 12  # full annual amount


@freeze_time("2026-06-15")
def test_expiring_days_until_expiry(db_session):
    today = date(2026, 6, 15)
    svc = _service(db_session)
    prop = make_property(db_session)
    make_renter(
        db_session,
        property_id=prop.id,
        lease_start=today - timedelta(days=300),
        lease_end=today + timedelta(days=45),
    )
    [row] = svc.get_expiring_leases(OWNER_A, days_until=90)
    assert row.days_until_expiry == 45

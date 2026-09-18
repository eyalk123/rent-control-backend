"""Unit tests for the open-ended lease top-up.

Split the way the service is: `extend_to_horizon` is pure arithmetic and tested on plain
dicts (the shape `tests/unit/test_lease_periods.py` uses), while `LeaseGenerationService`
is tested against the DB because what it mostly has to get right is *which rows it touches*.
"""
import json
from datetime import date

from app.repositories.renter_repository import RenterRepository
from app.services.lease_generation_service import (
    HORIZON_PERIODS,
    LeaseGenerationService,
    extend_to_horizon,
    remaining_periods,
)
from tests.factories import make_property, make_renter

START = date(2026, 1, 1)


def years(*amounts, months=None):
    """A lease-year list, the way the stored JSON blob looks."""
    rows = [{"amount": float(a), "type": "contract"} for a in amounts]
    if months is not None:
        rows[-1]["months"] = months
    return rows


def _service(session) -> LeaseGenerationService:
    return LeaseGenerationService(RenterRepository(session))


# --- remaining_periods: the horizon is counted from the CURRENT period ---

def test_remaining_counts_the_period_in_progress():
    """A lease created today with five periods is already correct.

    Counting only *future* periods would make every new lease grow a sixth year on its
    first night — the horizon has to include the one the lease is in.
    """
    assert remaining_periods(START, years(5000, 5000, 5000, 5000, 5000), START) == 5


def test_remaining_drops_by_one_on_each_anniversary():
    schedule = years(5000, 5000, 5000, 5000, 5000)
    assert remaining_periods(START, schedule, date(2026, 12, 31)) == 5
    assert remaining_periods(START, schedule, date(2027, 1, 1)) == 4
    assert remaining_periods(START, schedule, date(2028, 1, 1)) == 3


def test_remaining_honours_a_short_tail():
    """A period carries its own length; `months` absent means twelve."""
    assert remaining_periods(START, years(5000, 5000, months=3), date(2027, 2, 1)) == 1
    assert remaining_periods(START, years(5000, 5000, months=3), date(2027, 5, 1)) == 0


# --- extend_to_horizon: append only, priced by chaining ---

def test_full_horizon_is_returned_untouched():
    """Returns the same object, which is what lets the job skip the write entirely."""
    schedule = years(5000, 5000, 5000, 5000, 5000)
    assert extend_to_horizon(schedule, START, "none", 0, START) is schedule


def test_one_period_is_appended_when_one_anniversary_has_passed():
    schedule = years(5000, 5150, 5305, 5464, 5628)
    out = extend_to_horizon(schedule, START, "percent", 3, date(2027, 1, 1))

    assert len(out) == 6
    assert out[:5] == schedule  # nothing existing is rewritten
    assert out[5] == {"amount": 5797, "type": "contract", "generated": True}


def test_appended_periods_are_contract_type_and_marked_generated():
    out = extend_to_horizon(years(5000), START, "none", 0, START)
    added = out[1:]
    assert len(added) == HORIZON_PERIODS - 1
    assert all(row["type"] == "contract" for row in added)
    assert all(row["generated"] is True for row in added)


def test_a_corrected_amount_prices_every_generated_period_after_it():
    """The reason pricing chains off the previous period instead of `base * (1+r)**n`.

    The owner was told 3%, the landlord actually raised to 6,000 — the closed form would
    keep projecting off 5,000 and quietly discard the correction.
    """
    corrected = [
        {"amount": 5000.0, "type": "contract"},
        {"amount": 6000.0, "type": "contract", "rule": {"mode": "manual"}},
    ]
    # Mid-2027 only the second period is still running, so four are appended.
    out = extend_to_horizon(corrected, START, "percent", 10, date(2027, 6, 1))
    assert [row["amount"] for row in out[2:]] == [6600, 7260, 7986, 8785]


def test_fixed_mode_adds_a_flat_step():
    out = extend_to_horizon(years(5000), START, "fixed", 250, START)
    assert [row["amount"] for row in out] == [5000.0, 5250, 5500, 5750, 6000]


def test_none_mode_holds_the_rent_flat():
    out = extend_to_horizon(years(5000), START, "none", 0, START)
    assert [row["amount"] for row in out] == [5000.0, 5000, 5000, 5000, 5000]


def test_an_empty_schedule_is_left_alone():
    assert extend_to_horizon([], START, "percent", 3, START) == []


# --- the job: which rows it touches ---

def test_job_extends_an_open_ended_lease(db_session):
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        lease_years=years(5000, 5000),
        lease_start=START,
        open_ended=True,
        rent_escalation_mode="none",
    )

    summary = _service(db_session).run_lease_generation(today=START)

    db_session.refresh(renter)
    assert summary["renters_extended"] == 1
    assert summary["periods_added"] == 3
    assert len(json.loads(renter.lease_years)) == 5


def test_job_is_idempotent(db_session):
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        lease_years=years(5000, 5000),
        lease_start=START,
        open_ended=True,
        rent_escalation_mode="percent",
        rent_escalation_value=3,
    )
    service = _service(db_session)

    service.run_lease_generation(today=START)
    db_session.refresh(renter)
    first = renter.lease_years

    second_summary = service.run_lease_generation(today=START)
    db_session.refresh(renter)

    assert second_summary["renters_extended"] == 0
    assert renter.lease_years == first


def test_job_moves_both_end_dates(db_session):
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        lease_years=years(5000),
        lease_start=START,
        open_ended=True,
        rent_escalation_mode="none",
    )
    assert renter.lease_end == date(2027, 1, 1)

    _service(db_session).run_lease_generation(today=START)
    db_session.refresh(renter)

    # Five periods from the start, and `contract_end` tracks it because generated periods
    # are contract-typed — which is what keeps the expiry countdown out of range.
    assert renter.lease_end == date(2031, 1, 1)
    assert renter.contract_end == date(2031, 1, 1)


def test_an_extended_lease_is_still_active(db_session):
    """The regression the whole rolling-window design exists to prevent.

    `effective_lease_end()` is `coalesce(terminated_on, lease_end)`, and every "active
    tenant" query compares it `>= today`. Had open-ended meant a NULL `lease_end`, this
    renter would have dropped out of `get_active` in SQL, silently.
    """
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        lease_years=years(5000),
        lease_start=START,
        open_ended=True,
        rent_escalation_mode="none",
    )
    _service(db_session).run_lease_generation(today=START)

    active = RenterRepository(db_session).get_active(renter.owner_id)
    assert renter.id in [r.id for r in active]


def test_job_skips_a_terminated_lease(db_session):
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        lease_years=years(5000),
        lease_start=START,
        open_ended=True,
        rent_escalation_mode="none",
        terminated_on=date(2026, 6, 1),
    )
    before = renter.lease_years

    summary = _service(db_session).run_lease_generation(today=START)

    db_session.refresh(renter)
    assert summary["renters_examined"] == 0  # excluded by the query, not skipped in the loop
    assert renter.lease_years == before


def test_job_skips_a_lease_that_is_not_open_ended(db_session):
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        lease_years=years(5000),
        lease_start=START,
        rent_escalation_mode="none",
    )
    before = renter.lease_years

    _service(db_session).run_lease_generation(today=START)

    db_session.refresh(renter)
    assert renter.lease_years == before


def test_job_skips_custom_and_cpi_modes(db_session):
    """The API refuses to pair these with the switch, but a row predating that guard —
    or one written directly — must be left alone rather than mispriced."""
    prop = make_property(db_session)
    for mode in ("custom", "cpi"):
        renter = make_renter(
            db_session,
            property_id=prop.id,
            lease_years=years(5000),
            lease_start=START,
            open_ended=True,
            rent_escalation_mode=mode,
        )
        before = renter.lease_years
        _service(db_session).run_lease_generation(today=START)
        db_session.refresh(renter)
        assert renter.lease_years == before, mode


def test_job_survives_an_unreadable_schedule(db_session):
    prop = make_property(db_session)
    renter = make_renter(
        db_session,
        property_id=prop.id,
        lease_start=START,
        open_ended=True,
        rent_escalation_mode="none",
    )
    renter.lease_years = "{not json"
    db_session.commit()

    summary = _service(db_session).run_lease_generation(today=START)

    assert summary["renters_extended"] == 0

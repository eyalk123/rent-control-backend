"""Unit tests for PropertyService (real repos over the test session)."""
from datetime import date, timedelta

from app.repositories.property_repository import PropertyRepository
from app.repositories.renter_repository import RenterRepository
from app.services.property_service import PropertyService
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_property, make_renter

# get_property_renters returns only *active* leases (lease_start <= today <= lease_end).
_TODAY = date.today()
_ACTIVE_LEASE = dict(lease_start=_TODAY - timedelta(days=30), lease_end=_TODAY + timedelta(days=335))


def _service(db_session):
    return PropertyService(
        property_repository=PropertyRepository(db_session),
        renter_repository=RenterRepository(db_session),
    )


def test_get_property_renters_reports_monthly_rent_not_annual(db_session):
    """Regression: monthly_rent must equal the stored first-year amount, which is
    already a MONTHLY figure. The old code divided by 12 and reported 1000.0."""
    svc = _service(db_session)
    prop = make_property(db_session)
    # DEFAULT_LEASE_YEARS = [{"amount": 12000.0, "type": "contract"}] — a monthly rent.
    make_renter(
        db_session, property_id=prop.id, first_name="Dana", last_name="Levi", **_ACTIVE_LEASE
    )

    summaries = svc.get_property_renters(prop.id, OWNER_A)

    assert len(summaries) == 1
    assert summaries[0].monthly_rent == 12000.0  # not 1000.0


def test_get_property_renters_scoped_to_owner(db_session):
    """Another owner cannot read this property's renters — the repo returns None
    for a property it doesn't own."""
    svc = _service(db_session)
    prop = make_property(db_session, owner_id=OWNER_A)
    make_renter(db_session, owner_id=OWNER_A, property_id=prop.id, **_ACTIVE_LEASE)

    assert svc.get_property_renters(prop.id, OWNER_B) is None

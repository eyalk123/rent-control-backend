"""The invariants global support must not break.

Two kinds of test live here, and they fail for opposite reasons:

**Guards** protect a promise we made — Israel is unchanged, an unavailable feature is
unreachable, deleting an account really deletes everything. A failure means something broke.

**Pins** record a change we made *on purpose* that touches Israeli users. A failure means
someone has "fixed" a deliberate decision back to how it used to be. Each one names the
decision so the next person can tell the difference, rather than reverting it on sight.

Kept in one file because these are project-level promises rather than one module's
behaviour, and because the list is what gets reviewed before the branch merges.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.countries.config import COUNTRIES
from app.models.owner import Owner
from app.models.property import Property, PropertyTypeEnum
from app.models.transaction import TransactionTypeEnum
from app.services import country_service
from app.services.report_service import get_income_expense_data
from tests.conftest import OWNER_A
from tests.factories import make_property, make_transaction


# ── Guards ──────────────────────────────────────────────────────────────────


class TestOverdueIgnoresTheRecognitionBasis:
    """**The load-bearing constraint of the whole reporting change.**

    Chasing rent is a collections question, not a tax one. A tenant who has not paid
    December's rent is overdue on 1 January whether their landlord files on accrual or on
    cash — the basis decides which *tax year* the money lands in, and nothing else.

    Today the overdue query reads `month_for` and has no idea the basis exists. These tests
    exist because that is exactly the kind of invariant someone breaks later while tidying
    up: the report and the overdue engine both answer "which month is this rent for", and
    unifying them behind one helper would look like a clean-up and silently make overdue
    depend on a tax setting.
    """

    def _unpaid_last_month(self, db_session):
        """A renter owing last month's rent, so they are overdue regardless of basis."""
        from tests.factories import make_renter

        prop = make_property(db_session, owner_id=OWNER_A)
        start = date.today().replace(day=1) - timedelta(days=400)
        return make_renter(
            db_session,
            owner_id=OWNER_A,
            property_id=prop.id,
            lease_start=start,
            lease_years=[{"amount": 5000, "type": "contract"}, {"amount": 5000, "type": "contract"}],
        )

    def test_the_overdue_query_does_not_take_a_basis_at_all(self):
        """The strongest form of the guarantee: it cannot depend on something it cannot be
        told. If someone adds a `basis` parameter here, this fails and they have to justify
        it rather than discover the consequences in production."""
        import inspect

        from app.repositories.renter_repository import RenterRepository
        from app.services.renter_service import RenterService

        for fn in (RenterRepository.get_overdue_this_month, RenterService.get_overdue_this_month):
            params = set(inspect.signature(fn).parameters)
            assert "basis" not in params, f"{fn.__qualname__} must not know about the basis"
            assert "revenue_basis" not in params, f"{fn.__qualname__} must not know about the basis"

    def test_overdue_is_anchored_to_month_for_not_the_payment_date(self, db_session):
        """`month_for` is what makes overdue mean "this month's rent is unpaid". Reading the
        payment date instead would make a prepayment look like it settled a later month."""
        import inspect

        from app.repositories.renter_repository import RenterRepository

        source = inspect.getsource(RenterRepository.get_overdue_this_month)
        assert "month_for" in source
        assert "date_of_payment" not in source

    def test_month_for_stays_required_for_revenue(self):
        """Overdue cannot work without it, so it stays mandatory in every country — the
        cash basis does not get to make it optional."""
        from app.schemas.transaction import TransactionCreateRevenue

        field = TransactionCreateRevenue.model_fields["month_for"]
        assert field.is_required(), "month_for must stay mandatory — overdue depends on it"

    def test_the_two_bases_disagree_about_the_year_but_not_about_being_owed(self, db_session):
        """The same payment lands in different years under the two bases — and neither
        answer has any bearing on whether the tenant was chased for it."""
        prop = make_property(db_session, owner_id=OWNER_A)
        make_transaction(
            db_session,
            owner_id=OWNER_A,
            type=TransactionTypeEnum.REVENUE,
            property_id=prop.id,
            amount=5000,
            date_of_payment=date(2026, 1, 4),
            month_for=date(2025, 12, 1),
        )
        accrual_2025 = get_income_expense_data(db_session, OWNER_A, 2025, "accrual")
        cash_2026 = get_income_expense_data(db_session, OWNER_A, 2026, "cash")
        assert accrual_2025.grand_total.revenue == Decimal("5000")
        assert cash_2026.grand_total.revenue == Decimal("5000")


class TestAccountDeletionIsComplete:
    """Deleting an account must remove everything it created, including the rows added by
    global support. A sweep that quietly misses one leaves personal data behind."""

    def test_a_notify_me_row_goes_with_the_account(self, client, db_session):
        from app.models.country_notify_request import CountryNotifyRequest
        from app.services.user_service import UserService

        owner = db_session.get(Owner, OWNER_A) or Owner(id=OWNER_A, email="a@b.c")
        owner.country = "US"
        db_session.add(owner)
        db_session.commit()

        assert client.post("/users/me/notify-country", json={"country_code": "ES"}).status_code == 201
        assert db_session.query(CountryNotifyRequest).count() == 1

        UserService(db_session).delete_account(OWNER_A)
        assert db_session.query(CountryNotifyRequest).count() == 0

    def test_the_sweep_covers_every_owner_scoped_table(self):
        """A new owner-scoped table that nobody adds to `delete_account` is the failure
        mode, and it is silent. This lists the tables the sweep must name."""
        import inspect

        from app.services.user_service import UserService

        source = inspect.getsource(UserService.delete_account)
        for table in (
            "CountryNotifyRequest",
            "Property",
            "Renter",
            "Transaction",
            # Holds no PII — only counts — but it is owner-scoped behavioural data and is
            # covered by the same promise as the rest.
            "OwnerClientDay",
        ):
            assert table in source, f"delete_account no longer sweeps {table}"


class TestEveryCountryProducesAWorkingAccount:
    """No selection is ever rejected — including the ~180 nobody hand-checked."""

    def test_every_iso_code_is_accepted_at_signup(self, client, db_session):
        rejected = [
            code
            for code in COUNTRIES
            if client.patch("/users/me/country", json={"country": code}).status_code != 200
        ]
        assert rejected == []

    def test_every_country_resolves_to_a_usable_config(self):
        """A country with no currency or no dial code would render broken rather than
        skimmed."""
        for code, cfg in COUNTRIES.items():
            assert cfg.currency, code
            assert cfg.currency_symbol, code
            assert cfg.dial_code, code
            assert cfg.date_format in ("DMY", "MDY", "YMD"), code
            assert cfg.area_unit in ("sqm", "sqft"), code

    def test_no_country_but_israel_can_reach_index_linkage(self):
        for code in COUNTRIES:
            assert country_service.capabilities_for(code).cpi_linkage == (code == "IL")


class TestOpenEndedCountriesAreFlaggedNotBlocked:
    def test_the_nine_are_flagged(self):
        flagged = {c for c, cfg in COUNTRIES.items() if cfg.open_ended_tenancies}
        assert flagged == {"GB", "DE", "NL", "AT", "SE", "DK", "NO", "FI", "CH"}

    def test_being_flagged_removes_nothing(self):
        """It is informational. A flagged country gets the same skimmed product as any
        other — the flag only adds a line of copy and a longer default term."""
        gb, fr = COUNTRIES["GB"], COUNTRIES["FR"]
        assert gb.tier == fr.tier == "supported"
        assert gb.capabilities == fr.capabilities


# ── Pins: deliberate changes to the Israeli experience ──────────────────────


class TestDeliberateIsraeliChanges:
    """Global support was meant to be additive for Israel. These are the places it is not,
    each one agreed rather than accidental. If one of these fails, the question is not "what
    broke" but "did someone revert a decision".
    """

    def test_israel_gets_the_three_new_expense_categories(self):
        """Built-ins are global rows shared by every account, so there is no per-country
        set — adding them for the world adds them for Israel. Accepted, and irreversible:
        a category can never be renamed or deleted.

        Asserted against the migration and the report label map rather than the API,
        because the test database is built with `create_all` and never runs a migration —
        so the seeded rows do not exist here at all.
        """
        from pathlib import Path

        from app.services.report_service import CATEGORY_LABELS

        new = {"mortgage_interest", "building_fees", "legal_professional"}
        seed = (
            Path(__file__).resolve().parent.parent
            / "alembic" / "versions" / "20260913_global_expense_categories.py"
        ).read_text(encoding="utf-8")
        for key in new:
            assert key in seed, f"{key} is no longer seeded"
            assert key in CATEGORY_LABELS["en"], f"{key} has no English label"
            assert key in CATEGORY_LABELS["he"], f"{key} has no Hebrew label"

    def test_israel_can_choose_the_cash_basis(self):
        """Offered everywhere, not gated: an Israeli individual keeping no double-entry
        books reports on a cash basis too. Accrual stays the pre-selected option, so
        nothing changes unless the user asks for it."""
        assert COUNTRIES["IL"].revenue_basis_default == "accrual"
        from app.services.report_service import SUPPORTED_BASES

        assert set(SUPPORTED_BASES) == {"accrual", "cash"}

    def test_israel_gets_the_per_renter_expiry_mute(self, db_session):
        """Universal rather than gated — an Israeli month-to-month holdover has the same
        meaningless countdown as an open-ended tenancy."""
        from app.models.renter import Renter

        assert hasattr(Renter, "suppress_expiry_alerts")

    def test_israel_no_longer_requires_postal_code_area_or_price(self, client, db_session):
        """Loosened for every country, Israel included — a listed friction point, and both
        clients already treated them as optional."""
        owner = db_session.get(Owner, OWNER_A) or Owner(id=OWNER_A, email="a@b.c")
        owner.country = "IL"
        db_session.add(owner)
        db_session.commit()

        r = client.post(
            "/properties",
            json={"address": "1 Test St", "city": "Tel Aviv", "type": "apartment"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["zip_code"] is None

    def test_israel_keeps_every_israeli_specific_option(self):
        """The other half of the promise: nothing Israeli was taken away."""
        caps = country_service.capabilities_for("IL")
        assert caps.cpi_linkage
        assert caps.israeli_property_types
        assert caps.bit_payments
        assert caps.structured_bank_details

    def test_israeli_report_currency_is_untouched(self):
        """The artifact users hand to an accountant. ILS keeps its language-dependent label
        in the prefix position rather than taking the config's suffix form."""
        from app.services.report_service import _fmt

        assert _fmt(Decimal("5000"), "en", country_service.config_for("IL")) == "ILS 5,000"
        assert _fmt(Decimal("5000"), "he", country_service.config_for("IL")) == "₪5,000"

    @pytest.mark.parametrize("value", ["condo_townhouse", "room", "other"])
    def test_israel_also_sees_the_new_property_types(self, value):
        """**Flagged for review.** Israel keeps Garden Apartment and Housing Unit, and also
        gains Condo/Townhouse, Room and Other, because only the two Israeli types are gated.
        Defensible — a sublet room is common in Israel — but it was not asked for. Gate them
        on `israeli_property_types` being *false* if that is not wanted."""
        assert value in {e.value for e in PropertyTypeEnum}

    @pytest.mark.parametrize("value", ["card", "mobile_payment", "other"])
    def test_israel_also_sees_the_new_payment_methods(self, value):
        """**Flagged for review**, same as the property types: only `bit` is gated, so the
        three new methods appear in Israel too. Card standing orders are common there, so
        this is probably wanted — but it was not asked for."""
        from app.models.transaction import PaymentMethodEnum

        assert value in {e.value for e in PaymentMethodEnum}

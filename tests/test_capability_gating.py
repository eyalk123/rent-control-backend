"""Index linkage is off end to end for a country with no index behind it.

The spec's §10.3 is the point of this file: hiding the CPI option in the clients while the
API still accepts `escalation: 'cpi'` leaves a live path into a subsystem that has no data
source for the account. The lease would be written and then priced against Israeli index
readings that have nothing to do with it.

Three independent paths, tested separately, because closing one proves nothing about the
others: the API on create, the API on update, and the background job.
"""
import pytest

from app.models.owner import Owner
from app.models.property import Property, PropertyTypeEnum
from app.services import country_service
from tests.factories import OWNER_A


def _owner(db, country):
    owner = db.get(Owner, OWNER_A)
    if owner is None:
        owner = Owner(id=OWNER_A, email="a@b.c")
        db.add(owner)
    owner.country = country
    db.commit()
    return owner


def _property(db, country, owner_id=OWNER_A):
    prop = Property(
        owner_id=owner_id,
        address="1 Test St",
        city="Testville",
        zip_code="00000",
        type=PropertyTypeEnum.APARTMENT,
        sq_ft=80,
        purchase_price=0,
        country=country,
    )
    db.add(prop)
    db.commit()
    db.refresh(prop)
    return prop


def _renter_payload(property_id, **overrides):
    payload = {
        "first_name": "Test",
        "last_name": "Renter",
        "phone": "050-000-0000",
        "property_id": property_id,
        "lease_start": "2026-01-01",
        "lease_years": [{"amount": 5000, "type": "contract"}],
        "base_rent": 5000,
    }
    payload.update(overrides)
    return payload


class TestTheCountrySetIsDerivedNotHardcoded:
    def test_only_israel_has_index_linkage_today(self):
        assert country_service.countries_with_index_linkage() == ["IL"]

    def test_the_set_comes_from_the_flags(self):
        """Adding a market must be one flag, not a list someone remembers to update."""
        from app.countries.config import COUNTRIES

        derived = {c for c, cfg in COUNTRIES.items() if cfg.capabilities.cpi_linkage}
        assert set(country_service.countries_with_index_linkage()) == derived


class TestApiRejectsOnCreate:
    def test_non_il_property_cannot_take_cpi_escalation(self, client, db_session):
        _owner(db_session, "US")
        prop = _property(db_session, "US")
        r = client.post(
            "/renters",
            json=_renter_payload(prop.id, rent_escalation_mode="cpi"),
        )
        assert r.status_code == 422
        assert "United States" in r.json()["detail"]

    def test_non_il_property_cannot_take_a_cpi_year_rule(self, client, db_session):
        """The per-year rule inside `custom` is the other way in, and it is easy to miss."""
        _owner(db_session, "US")
        prop = _property(db_session, "US")
        r = client.post(
            "/renters",
            json=_renter_payload(
                prop.id,
                rent_escalation_mode="custom",
                lease_years=[
                    {"amount": 5000, "type": "contract"},
                    {"amount": 5000, "type": "contract", "rule": {"mode": "cpi"}},
                ],
            ),
        )
        assert r.status_code == 422

    def test_israeli_property_still_accepts_cpi(self, client, db_session):
        """The primary regression risk — Israel must be completely unaffected."""
        _owner(db_session, "IL")
        prop = _property(db_session, "IL")
        r = client.post("/renters", json=_renter_payload(prop.id, rent_escalation_mode="cpi"))
        assert r.status_code == 201, r.text

    def test_the_country_comes_from_the_property_not_the_account(self, client, db_session):
        """Rules attach to where the building is. An Israeli account with a US property
        must not be able to price that property against Israeli index data."""
        _owner(db_session, "IL")
        prop = _property(db_session, "US")
        r = client.post("/renters", json=_renter_payload(prop.id, rent_escalation_mode="cpi"))
        assert r.status_code == 422

    @pytest.mark.parametrize("mode", ["none", "percent", "fixed"])
    def test_the_other_modes_are_untouched(self, client, db_session, mode):
        _owner(db_session, "US")
        prop = _property(db_session, "US")
        r = client.post(
            "/renters",
            json=_renter_payload(prop.id, rent_escalation_mode=mode, rent_escalation_value=3),
        )
        assert r.status_code == 201, r.text


class TestApiRejectsOnUpdate:
    def test_cpi_cannot_be_introduced_by_an_edit(self, client, db_session):
        _owner(db_session, "US")
        prop = _property(db_session, "US")
        created = client.post(
            "/renters", json=_renter_payload(prop.id, rent_escalation_mode="none")
        )
        assert created.status_code == 201
        r = client.patch(
            f"/renters/{created.json()['id']}", json={"rent_escalation_mode": "cpi"}
        )
        assert r.status_code == 422

    def test_an_unrelated_edit_is_not_blocked(self, client, db_session):
        """Guard what the request asks for, not what the row holds. Blocking every edit to
        a lease that already stores `cpi` would lock the owner out of their own record
        without repairing the data."""
        _owner(db_session, "IL")
        prop = _property(db_session, "IL")
        created = client.post(
            "/renters", json=_renter_payload(prop.id, rent_escalation_mode="cpi")
        )
        assert created.status_code == 201
        r = client.patch(f"/renters/{created.json()['id']}", json={"phone": "050-111-2222"})
        assert r.status_code == 200, r.text


class TestTheJobIsScopedNotNoOpped:
    def test_a_non_il_lease_is_never_selected(self, client, db_session):
        """Skipping rows after loading them is not the same as never selecting them."""
        from app.repositories.renter_repository import RenterRepository

        _owner(db_session, "IL")
        il_prop = _property(db_session, "IL")
        client.post("/renters", json=_renter_payload(il_prop.id, rent_escalation_mode="cpi"))

        us_prop = _property(db_session, "US")
        client.post(
            "/renters", json=_renter_payload(us_prop.id, rent_escalation_mode="percent",
                                             rent_escalation_value=3)
        )
        # Force a stored `cpi` on the US lease the way a bug or a country change could,
        # bypassing the API guard — the job must still not pick it up.
        from app.models.renter import Renter

        us_renter = db_session.query(Renter).filter(Renter.property_id == us_prop.id).one()
        us_renter.rent_escalation_mode = "cpi"
        db_session.commit()

        selected = RenterRepository(db_session).get_by_escalation_modes(
            ["cpi", "custom"], countries=country_service.countries_with_index_linkage()
        )
        property_ids = {r.property_id for r in selected}
        assert il_prop.id in property_ids
        assert us_prop.id not in property_ids

    def test_unscoped_call_still_returns_everything(self, client, db_session):
        """`countries=None` keeps the old behaviour, so nothing else that calls this
        changes meaning."""
        from app.repositories.renter_repository import RenterRepository

        _owner(db_session, "IL")
        prop = _property(db_session, "IL")
        client.post("/renters", json=_renter_payload(prop.id, rent_escalation_mode="cpi"))
        assert RenterRepository(db_session).get_by_escalation_modes(["cpi", "custom"])


class TestTheAssistant:
    def test_ten_tools_become_nine_without_index_linkage(self):
        from app.services.agent_tools import TOOL_SCHEMAS, tool_schemas_for

        assert len(TOOL_SCHEMAS) == 10
        assert len(tool_schemas_for("IL")) == 10
        assert len(tool_schemas_for("US")) == 9

    def test_the_cpi_tool_is_the_one_removed(self):
        from app.services.agent_tools import tool_schemas_for

        names = {s["name"] for s in tool_schemas_for("US")}
        assert "explain_cpi" not in names
        assert "explain_cpi" in {s["name"] for s in tool_schemas_for("IL")}

    def test_dispatch_refuses_it_even_if_the_model_asks(self, db_session):
        """The schemas are filtered before the model sees them, so this should be
        unreachable — but the tool name arrives from model output, and a tool that runs the
        index engine for an account with no index is not left resting on the prompt."""
        from app.services.agent_tools import AgentTools

        _owner(db_session, "US")
        out = AgentTools(db_session).dispatch("explain_cpi", OWNER_A, {"renter_id": 1})
        assert "error" in out

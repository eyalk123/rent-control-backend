"""The country endpoints: the picker's data and the signup choice."""


class TestListCountries:
    def test_returns_the_whole_table_name_ordered(self, client):
        r = client.get("/countries")
        assert r.status_code == 200
        body = r.json()
        assert len(body) > 200
        assert [c["name"] for c in body] == sorted(c["name"] for c in body)

    def test_row_carries_everything_a_client_needs_to_format(self, client):
        body = {c["country_code"]: c for c in client.get("/countries").json()}
        us = body["US"]
        assert us["currency"] == "USD"
        assert us["currency_symbol_position"] == "prefix"
        assert us["date_format"] == "MDY"
        assert us["area_unit"] == "sqft"
        assert us["revenue_basis_default"] == "cash"

    def test_registry_fields_are_i18n_keys_not_labels(self, client):
        body = {c["country_code"]: c for c in client.get("/countries").json()}
        assert body["GB"]["registry_key_1"] == "property.registry.title_number"
        # Israel overrides nothing, so the clients fall back to their own locale files and
        # the Israeli rendering path stays untouched.
        assert body["IL"]["registry_key_1"] is None

    def test_israel_is_the_only_country_with_capabilities(self, client):
        for c in client.get("/countries").json():
            caps = c["capabilities"]
            assert any(caps.values()) == (c["country_code"] == "IL"), c["country_code"]

    def test_single_country_lookup(self, client):
        r = client.get("/countries/IL")
        assert r.status_code == 200
        assert r.json()["tier"] == "native"

    def test_unknown_code_404s_rather_than_falling_back(self, client):
        """Reads inside the app degrade to safe defaults so pages render. A lookup must
        not, or a typo is hidden at the one moment it is still cheap to fix."""
        assert client.get("/countries/XX").status_code == 404

    def test_no_country_but_israel_carries_index_linkage(self, client):
        body = {c["country_code"]: c for c in client.get("/countries").json()}
        assert body["IL"]["capabilities"]["cpi_linkage"] is True
        for code in ("US", "GB", "FR", "DE", "ES"):
            assert body[code]["capabilities"]["cpi_linkage"] is False


class TestSetCountry:
    def test_sets_and_returns_the_profile(self, client):
        r = client.patch("/users/me/country", json={"country": "US"})
        assert r.status_code == 200
        assert r.json()["country"] == "US"
        assert client.get("/users/me").json()["country"] == "US"

    def test_lowercase_is_normalized(self, client):
        assert client.patch("/users/me/country", json={"country": "gb"}).json()["country"] == "GB"

    def test_unknown_code_is_rejected(self, client):
        assert client.patch("/users/me/country", json={"country": "XX"}).status_code == 422

    def test_malformed_code_is_rejected(self, client):
        assert client.patch("/users/me/country", json={"country": "USA"}).status_code == 422


class TestListCurrencies:
    def test_returns_the_whole_table_name_ordered(self, client):
        r = client.get("/currencies")
        assert r.status_code == 200
        body = r.json()
        assert len(body) > 100
        assert [c["name"] for c in body] == sorted(c["name"] for c in body)

    def test_a_currency_with_no_glyph_carries_its_code_as_the_symbol(self, client):
        """What lets the picker list every currency rather than only the pretty ones — a
        landlord in Dakar can name their own money, plainly."""
        body = {c["code"]: c for c in client.get("/currencies").json()}
        assert body["USD"]["symbol"] == "$"
        assert body["XOF"]["symbol"] == "XOF"

    def test_decimals_are_carried_for_currencies_with_no_minor_unit(self, client):
        body = {c["code"]: c for c in client.get("/currencies").json()}
        assert body["JPY"]["decimals"] == 0
        assert body["USD"]["decimals"] == 2

    def test_unknown_code_404s_rather_than_falling_back(self, client):
        assert client.get("/currencies/XXX").status_code == 404


class TestSetPreferences:
    def test_sets_both_and_returns_the_profile(self, client):
        r = client.patch("/users/me/preferences", json={"currency": "USD", "language": "en"})
        assert r.status_code == 200
        assert r.json()["currency"] == "USD"
        assert r.json()["language"] == "en"

    def test_an_omitted_field_is_left_alone_not_cleared(self, client):
        """The signup gate sends both; the Settings rows send one at a time."""
        client.patch("/users/me/preferences", json={"currency": "USD", "language": "he"})
        client.patch("/users/me/preferences", json={"language": "en"})
        body = client.get("/users/me").json()
        assert body == {**body, "currency": "USD", "language": "en"}

    def test_lowercase_currency_is_normalized(self, client):
        assert client.patch("/users/me/preferences", json={"currency": "eur"}).json()["currency"] == "EUR"

    def test_unknown_currency_is_rejected(self, client):
        assert client.patch("/users/me/preferences", json={"currency": "XXX"}).status_code == 422

    def test_currency_locks_once_the_account_has_a_property(self, client):
        """The rule that protects recorded amounts.

        `properties.currency_code` is frozen at creation and every transaction snapshots
        it, so a later change would relabel stored amounts without re-denominating them —
        a ₪5,000 rent silently reading as $5,000."""
        client.patch("/users/me/preferences", json={"currency": "ILS"})
        client.post("/properties", json={"address": "1 Test St", "city": "Tel Aviv", "type": "apartment"})

        r = client.patch("/users/me/preferences", json={"currency": "USD"})
        assert r.status_code == 409
        assert client.get("/users/me").json()["currency"] == "ILS"

    def test_resending_the_current_currency_stays_harmless(self, client):
        """Only a *change* is refused. Accepting the pre-filled default re-sends the same
        value, and that must not start failing the day a property exists."""
        client.patch("/users/me/preferences", json={"currency": "ILS"})
        client.post("/properties", json={"address": "1 Test St", "city": "Tel Aviv", "type": "apartment"})
        assert client.patch("/users/me/preferences", json={"currency": "ILS"}).status_code == 200

    def test_language_never_locks(self, client):
        """Nothing is stored in a language, so there is nothing to misrepresent."""
        client.post("/properties", json={"address": "1 Test St", "city": "Tel Aviv", "type": "apartment"})
        assert client.patch("/users/me/preferences", json={"language": "he"}).status_code == 200


class TestPropertyTakesTheChosenCurrency:
    """`currency_code` is not on the property read schema — nothing in either client needs
    it while an account is one currency — so these assert against the column itself."""

    @staticmethod
    def _stored_currency(db_session, property_id: int) -> str:
        from app.models.property import Property

        return db_session.get(Property, property_id).currency_code

    def test_a_chosen_currency_reaches_the_property(self, client, db_session):
        """The one place the choice has to land: the column is frozen at creation, so this
        is what every transaction on that property will snapshot."""
        client.patch("/users/me/country", json={"country": "IL"})
        client.patch("/users/me/preferences", json={"currency": "USD"})

        r = client.post("/properties", json={"address": "1 Test St", "city": "Tel Aviv", "type": "apartment"})
        assert r.status_code == 201
        assert self._stored_currency(db_session, r.json()["id"]) == "USD"

    def test_no_choice_falls_through_to_the_country(self, client, db_session):
        client.patch("/users/me/country", json={"country": "IL"})
        r = client.post("/properties", json={"address": "2 Test St", "city": "Haifa", "type": "apartment"})
        assert self._stored_currency(db_session, r.json()["id"]) == "ILS"

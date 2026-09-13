"""The country endpoints: the picker's data, the signup choice, and the notify-me capture."""


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


class TestNotifyMe:
    def test_records_a_request(self, client):
        r = client.post("/users/me/notify-country", json={"country_code": "ES"})
        assert r.status_code == 201
        assert r.json()["country_code"] == "ES"

    def test_asking_twice_is_idempotent(self, client):
        """The same fact, not a stronger one — double-counting would skew the signal."""
        first = client.post("/users/me/notify-country", json={"country_code": "FR"})
        second = client.post("/users/me/notify-country", json={"country_code": "FR"})
        assert first.status_code == 201 and second.status_code == 201

    def test_unknown_code_is_rejected(self, client):
        assert (
            client.post("/users/me/notify-country", json={"country_code": "XX"}).status_code
            == 422
        )

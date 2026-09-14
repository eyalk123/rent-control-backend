from pydantic import BaseModel, ConfigDict, Field, field_validator


class CapabilitiesRead(BaseModel):
    """What this country's accounts may do. The clients gate their UI on these.

    Server-side enforcement is what actually matters — a flag hidden in the client while
    the API still accepts the value is a live path to a subsystem with no data behind it —
    but the clients need the same answer to avoid offering a control that will be rejected.
    """

    cpi_linkage: bool
    tax_tracks: bool
    israeli_property_types: bool
    bit_payments: bool
    structured_bank_details: bool

    model_config = ConfigDict(from_attributes=True)


class CountryRead(BaseModel):
    """One row of the country table, as the signup picker and the formatters see it.

    ``registry_key_1`` / ``registry_key_2`` are **i18n keys, not labels** — the country
    decides which concept applies, the client's locale file says it in the reader's
    language. ``None`` means "no override, use your own translation files", which is what
    Israel uses so its rendering stays exactly as it was.
    """

    country_code: str
    name: str
    tier: str
    open_ended_tenancies: bool

    currency: str
    currency_symbol: str
    currency_symbol_position: str
    locale: str
    date_format: str
    number_format: str
    area_unit: str
    revenue_basis_default: str
    default_comms_channel: str
    dial_code: str
    has_postal_codes: bool

    registry_key_1: str | None
    registry_key_2: str | None

    capabilities: CapabilitiesRead

    model_config = ConfigDict(from_attributes=True)


class CountryUpdate(BaseModel):
    """The signup country choice. Asked once, never again in normal use."""

    country: str = Field(min_length=2, max_length=2)

    @field_validator("country")
    @classmethod
    def normalize(cls, v: str) -> str:
        return v.strip().upper()


class CountryNotifyRequestCreate(BaseModel):
    """"Notify me when you add {Country}". Optional; nothing depends on it."""

    country_code: str = Field(min_length=2, max_length=2)

    @field_validator("country_code")
    @classmethod
    def normalize(cls, v: str) -> str:
        return v.strip().upper()


class CountryNotifyRequestRead(BaseModel):
    country_code: str

    model_config = ConfigDict(from_attributes=True)

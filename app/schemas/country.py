from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    currency_symbol_spaced: bool
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

    #: i18n keys for the index-linked escalation mode — its label in the rent-change picker
    #: and the one-line note under it. Keys for the same reason the registry ones are: every
    #: market's clause is "the official index", but Israel's is the מדד and Spain's is the
    #: IPC, and the language alone cannot say which. ``None`` where the country has no index,
    #: which is also where ``capabilities.cpi_linkage`` is off.
    #:
    #: The series id, its sources and its history floor are deliberately **not** here. They
    #: are how the server fetches a number; a client has no use for them and publishing them
    #: would invite one.
    index_label_key: str | None = None
    index_note_key: str | None = None

    capabilities: CapabilitiesRead

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _flatten_index_series(cls, data):
        """Lift the two i18n keys out of ``CountryConfig.index_series``.

        Here rather than in the routers so every path that serialises a country — the list,
        the single lookup, anything added later — gets them without remembering to.
        """
        if isinstance(data, dict):
            return data
        values = {
            field: getattr(data, field) for field in cls.model_fields if hasattr(data, field)
        }
        series = getattr(data, "index_series", None)
        values["index_label_key"] = series.label_key if series else None
        values["index_note_key"] = series.note_key if series else None
        return values


class CountryUpdate(BaseModel):
    """The signup country choice. Asked once, never again in normal use."""

    country: str = Field(min_length=2, max_length=2)

    @field_validator("country")
    @classmethod
    def normalize(cls, v: str) -> str:
        return v.strip().upper()


class CurrencyRead(BaseModel):
    """One row of the currency table, as the signup picker sees it.

    ``symbol`` is the code itself for a currency with no glyph, which is what lets the
    picker list every currency rather than only the pretty ones.

    ``decimals`` is a display *cap*, never a minimum: it exists so a JPY account is not
    shown a fraction of a unit that does not exist, not to force two places on everything
    else.
    """

    code: str
    name: str
    symbol: str
    decimals: int
    default_symbol_position: str

    model_config = ConfigDict(from_attributes=True)


class PreferencesUpdate(BaseModel):
    """The two choices made beside the country, and changeable after it.

    Both optional: the gate sends them together, Settings sends one at a time, and an
    omitted field is left alone rather than cleared.
    """

    currency: str | None = Field(default=None, min_length=3, max_length=3)
    language: str | None = Field(default=None, min_length=2, max_length=5)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, v: str | None) -> str | None:
        return v.strip().upper() if v else v

    @field_validator("language")
    @classmethod
    def normalize_language(cls, v: str | None) -> str | None:
        return v.strip().lower() if v else v

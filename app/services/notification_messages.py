"""Localized copy for push notifications.

The reminder job pushes once per device locale, so each device's title and body
are rendered in its app language. Unknown or unset locales fall back to English.
"""

from datetime import date

from app.services import country_service, money_format

DEFAULT_LOCALE = "en"
_SUPPORTED = ("en", "he")

#: (currency, grouping, symbol spacing) — the same triple the agent passes around.
MoneyStyle = tuple[country_service.EffectiveCurrency, str, bool]

#: Israel, resolved lazily so importing this module does not build the country table.
_DEFAULT_STYLE: MoneyStyle | None = None


def default_style() -> MoneyStyle:
    """Israel's style, for a caller that has no owner to resolve."""
    global _DEFAULT_STYLE
    if _DEFAULT_STYLE is None:
        config = country_service.config_for(None)
        _DEFAULT_STYLE = (
            country_service.effective_currency(None),
            config.number_format,
            config.currency_symbol_spaced,
        )
    return _DEFAULT_STYLE

_MESSAGES = {
    "overdue": {
        "en": {
            "title": "Rent overdue",
            "body": "Rent from {label} is overdue.",
        },
        "he": {
            "title": "שכר דירה באיחור",
            "body": "שכר הדירה של {label} באיחור.",
        },
    },
    "lease_expiring": {
        "en": {
            "title": "Lease expiring",
            "body": "Lease for {label} expires in {days} days.",
        },
        "he": {
            "title": "חוזה מסתיים",
            "body": "החוזה של {label} מסתיים בעוד {days} ימים.",
        },
    },
    # The confirmed change: the amount is set, and this is what the owner tells the
    # tenant. Both figures plus the date, because the confirmation can land weeks after
    # the period boundary — "it went up" without a date is not actionable.
    "cpi_rent_change": {
        "en": {
            "title": "Rent changed",
            "body": "Rent for {label} changed from {old} to {new} ({delta}) effective {date}.",
        },
        "he": {
            "title": "שכר הדירה השתנה",
            "body": "שכר הדירה של {label} השתנה מ-{old} ל-{new} ({delta}) החל מ-{date}.",
        },
    },
    # The heads-up, fired before the boundary month's index is published — hence an
    # estimate, and it says so.
    "cpi_rent_change_upcoming": {
        "en": {
            "title": "Rent changing soon",
            "body": (
                "Rent for {label} changes on {date} — estimated {new} ({delta}). "
                "Final amount confirmed once the index publishes."
            ),
        },
        "he": {
            "title": "שכר הדירה עומד להשתנות",
            "body": (
                "שכר הדירה של {label} משתנה ב-{date} — הערכה {new} ({delta}). "
                "הסכום הסופי ייקבע עם פרסום המדד."
            ),
        },
    },
    # One push instead of a burst: the index publishes for the whole portfolio at once,
    # so an owner with many CPI leases would otherwise get a pile of notifications in
    # the same second.
    "cpi_rent_change_digest": {
        "en": {
            "title": "Rent changed",
            "body": "CPI rent changes for {count} renters.",
        },
        "he": {
            "title": "שכר הדירה השתנה",
            "body": "עדכוני מדד לשכר הדירה של {count} שוכרים.",
        },
    },
}

# The symbol's side comes from the country table, not from a comment here. This used to
# assert "₪ sits before the number in both languages", which the table has never agreed
# with — ILS is a *suffix* currency (see `tests/test_regression_gates.py`), and it is what
# every screen in the app prints. Direction is an RTL rendering concern and separate.
def format_amount(value: float | None, style: MoneyStyle | None = None) -> str:
    if value is None:
        return "—"
    currency, number_format, spaced = style or default_style()
    return money_format.format_money(round(value), currency, number_format, spaced)


def format_delta(
    delta: float | None, percent: float | None, style: MoneyStyle | None = None
) -> str:
    """A signed money delta with its percentage, e.g. ``+240₪, +4.8%``."""
    if delta is None:
        return "—"
    sign = "+" if delta >= 0 else "−"
    out = f"{sign}{format_amount(abs(round(delta)), style)}"
    if percent is not None:
        out += f", {sign}{abs(percent):.1f}%"
    return out


def normalize_locale(locale: str | None) -> str:
    """Map an arbitrary device locale to one we have copy for, defaulting to English."""
    return locale if locale in _SUPPORTED else DEFAULT_LOCALE


def render_overdue(locale: str, *, label: str) -> tuple[str, str]:
    copy = _MESSAGES["overdue"][normalize_locale(locale)]
    return copy["title"], copy["body"].format(label=label)


def render_lease_expiring(locale: str, *, label: str, days: int) -> tuple[str, str]:
    copy = _MESSAGES["lease_expiring"][normalize_locale(locale)]
    return copy["title"], copy["body"].format(label=label, days=days)


def render_cpi_rent_change(
    locale: str, *, label: str, data: dict, style: MoneyStyle | None = None
) -> tuple[str, str]:
    """One renter's CPI change, in whichever stage the row carries.

    ``style`` defaults to Israel, which is not merely a safe fallback here: CPI linkage is
    capability-gated and ``renter_service._guard_index_linkage`` refuses to create an
    index-linked lease outside a country that has it, so today Israel is the only account
    that can ever reach this message. The parameter exists for the second such country.
    """
    upcoming = data.get("stage") == "upcoming"
    key = "cpi_rent_change_upcoming" if upcoming else "cpi_rent_change"
    copy = _MESSAGES[key][normalize_locale(locale)]
    return copy["title"], copy["body"].format(
        label=label,
        old=format_amount(data.get("old_amount"), style),
        new=format_amount(data.get("new_amount"), style),
        delta=format_delta(data.get("delta"), data.get("delta_percent"), style),
        date=_format_date(data.get("effective_date")),
    )


def render_cpi_digest(locale: str, *, count: int) -> tuple[str, str]:
    copy = _MESSAGES["cpi_rent_change_digest"][normalize_locale(locale)]
    return copy["title"], copy["body"].format(count=count)


def _format_date(iso: str | None) -> str:
    """``2026-03-01`` -> ``1 Mar 2026``. Day-first suits both locales, and the month
    name sidesteps the DD/MM vs MM/DD ambiguity in a string with no other context.

    Built by hand rather than with ``strftime``: the zero-stripping directive differs
    between platforms (``%-d`` vs ``%#d``) and the month name would follow the server
    locale rather than the device's."""
    if not iso:
        return "—"
    try:
        d = date.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

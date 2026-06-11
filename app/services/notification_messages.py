"""Localized copy for push notifications.

The reminder job pushes once per device locale, so each device's title and body
are rendered in its app language. Unknown or unset locales fall back to English.
"""

DEFAULT_LOCALE = "en"
_SUPPORTED = ("en", "he")

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
}


def normalize_locale(locale: str | None) -> str:
    """Map an arbitrary device locale to one we have copy for, defaulting to English."""
    return locale if locale in _SUPPORTED else DEFAULT_LOCALE


def render_overdue(locale: str, *, label: str) -> tuple[str, str]:
    copy = _MESSAGES["overdue"][normalize_locale(locale)]
    return copy["title"], copy["body"].format(label=label)


def render_lease_expiring(locale: str, *, label: str, days: int) -> tuple[str, str]:
    copy = _MESSAGES["lease_expiring"][normalize_locale(locale)]
    return copy["title"], copy["body"].format(label=label, days=days)

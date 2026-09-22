"""Emails an in-app support submission to the product owner, via Resend.

There is no support inbox and no admin screen. The product owner reads these in
their own mail client and replies from it, which is why ``reply_to`` carries the
submitter's address: hitting Reply answers the user directly, in the ordinary
inbox they already read, with no second feed to learn.

**What goes in the body and what goes in an attachment is a privacy decision, not
a layout one.** Replying quotes the body back to the user and silently drops
attachments, so the body holds only what the user already knows — their own
words, their name, the type they chose — and every internal identifier rides in
``diagnostics.txt``. Get that backwards and a routine reply pastes an owner id
into a customer's mailbox. The screenshots are attachments for the same reason,
and for one more: they never come back to the sender in a quote.

Unlike ``push_service``, a failure here is **not** swallowed. The caller turns it
into a 502 so the submitter is asked to retry, because a message we lost is a
message nobody would ever go looking for.
"""
import base64
import logging

import requests

from app.config import settings
from app.models.support_message import SupportMessage, SupportMessageTypeEnum
from app.schemas.support_message import ScreenshotIn

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 10

_TYPE_LABELS = {
    SupportMessageTypeEnum.BUG: "Bug",
    SupportMessageTypeEnum.QUESTION: "Question",
    SupportMessageTypeEnum.SUGGESTION: "Suggestion",
}


class SupportEmailError(RuntimeError):
    """Raised when the submission could not be handed to Resend."""


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _subject(entry: SupportMessage, submitter_name: str | None) -> str:
    """``[Bug] first line of the message — Dani Cohen``.

    The prefix survives into ``Re:`` when the owner replies, which reads like a
    tracked ticket rather than a leak — unlike anything in the diagnostics.
    """
    label = _TYPE_LABELS.get(entry.type, str(entry.type))
    snippet = " ".join(entry.message.split())[:60].strip()
    who = submitter_name or "an owner"
    return f"[{label}] {snippet} — {who}" if snippet else f"[{label}] — {who}"


def _context_footer(entry: SupportMessage) -> str:
    """The handful of facts that are safe to be quoted back at the sender.

    Everything here describes the sender's *own* device and account settings —
    which client, which build, which language, which country. They already know
    all of it, so a reply that quotes it tells them nothing they did not have.

    The identifiers deliberately excluded are the whole point: owner id, message
    id and email address stay in ``diagnostics.txt``, which a reply cannot quote.
    Adding one of those here would put it in a customer's mailbox the next time
    anyone hits Reply, so :func:`_body` is the wrong place for anything that
    identifies a *person* rather than a *build*.

    Empty fields are dropped rather than printed as a dash — a submission from a
    local dev build has no version, and a line of placeholders reads like
    something is broken.
    """
    parts = [
        entry.client_app,
        entry.client_version,
        entry.language,
        entry.country,
    ]
    return " · ".join(part for part in parts if part)


def _body(entry: SupportMessage, submitter_name: str | None) -> str:
    """The user's own words, their name, the type — and the context footer.

    Nothing here is a problem if it is quoted back to them on reply, which is
    exactly the test any new line added to this function has to pass. See
    :func:`_context_footer` before putting anything else in it.
    """
    label = _TYPE_LABELS.get(entry.type, str(entry.type))
    body = f"{submitter_name or 'An owner'}\n{label}\n\n{entry.message}\n"
    footer = _context_footer(entry)
    return f"{body}\n—\n{footer}\n" if footer else body


def _diagnostics(entry: SupportMessage, submitter_email: str | None) -> str:
    plain = [
        ("Message id", entry.id),
        ("Owner id", entry.owner_id),
        ("Email", submitter_email or "—"),
        ("Type", entry.type.value),
        ("Client", entry.client_app or "—"),
        ("Platform", entry.client_platform or "—"),
        ("App version", entry.client_version or "—"),
        ("Language", entry.language or "—"),
        ("Country", entry.country or "—"),
        ("Screenshots", entry.screenshot_count),
        ("Submitted (UTC)", entry.created_at.isoformat(timespec="seconds")),
    ]
    width = max(len(label) for label, _ in plain)
    lines = [f"{label.ljust(width)}  {value}" for label, value in plain]
    return "RentVance — support message diagnostics\n\n" + "\n".join(lines) + "\n"


def _attachments(
    entry: SupportMessage,
    submitter_email: str | None,
    screenshots: list[ScreenshotIn],
) -> list[dict]:
    items = [
        {
            "filename": "diagnostics.txt",
            "content": _b64(_diagnostics(entry, submitter_email).encode("utf-8")),
            "content_type": "text/plain",
        }
    ]
    for index, shot in enumerate(screenshots, start=1):
        items.append(
            {
                "filename": shot.filename or f"screenshot-{index}",
                # Already validated as decodable base64 by the schema, and passed
                # through untouched — decoding it here only to re-encode it would
                # double the peak memory for no gain.
                "content": shot.data,
                "content_type": shot.content_type,
            }
        )
    return items


def send_support_email(
    entry: SupportMessage,
    *,
    submitter_name: str | None,
    submitter_email: str | None,
    screenshots: list[ScreenshotIn],
) -> None:
    """Hand the submission to Resend. Raises :class:`SupportEmailError` on failure."""
    if not settings.RESEND_API_KEY or not settings.SUPPORT_EMAIL_TO:
        logger.warning("Support email not configured (RESEND_API_KEY / SUPPORT_EMAIL_TO)")
        raise SupportEmailError("support email is not configured")

    payload = {
        "from": settings.RESEND_FROM_ADDRESS,
        "to": [settings.SUPPORT_EMAIL_TO],
        "subject": _subject(entry, submitter_name),
        "text": _body(entry, submitter_name),
        "attachments": _attachments(entry, submitter_email, screenshots),
    }
    if submitter_email:
        # The whole point: Reply in the mail client answers the user, not us.
        # Their address is never put in ``from`` — that would fail SPF and land
        # the notification in spam.
        payload["reply_to"] = submitter_email

    headers = {
        "Authorization": f"Bearer {settings.RESEND_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            RESEND_URL, json=payload, headers=headers, timeout=_TIMEOUT_SECONDS
        )
        response.raise_for_status()
    except (requests.RequestException, ValueError) as exc:
        # Never log the message text or the submitter's address — the id is enough
        # to find the row, and the row is the durable copy.
        logger.warning("Support message %s failed to send: %s", entry.id, exc)
        raise SupportEmailError("support email send failed") from exc

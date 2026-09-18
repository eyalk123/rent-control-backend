"""An in-app support submission — a bug report, a question or a suggestion.

The product has no support inbox and no admin screen for these. A submission is
emailed to the product owner, who replies from their own mail client; the reply
reaches the user's ordinary inbox rather than a second feed inside the app. This
row is therefore **not** the delivery mechanism — it is the durable record that
survives a failed send, so a submission is never lost just because Resend was
unreachable for ten seconds.

**No image bytes and no image URLs are stored here.** Screenshots arrive base64
in the request body, are attached straight to the outgoing email and are then
dropped on the floor. A screenshot of this app shows renter names, addresses,
phone numbers and rent amounts — third parties who never agreed to anything with
us — so the only copy that outlives the request is the one in the recipient's
mailbox. ``screenshot_count`` is kept so a row can still say *"they attached two
and the email carried none"* without retaining the images to prove it.

``email_sent_at`` is set only on a confirmed 2xx from Resend. A row with it null
is a submission the sender was told to retry (the route answers 502 rather than
swallowing the failure), because with no admin screen a silently-eaten message is
one nobody would ever go looking for.
"""
import enum

from sqlalchemy import Column, DateTime, Enum, Integer, String, Text

from app.clock import utc_now_naive
from app.models.base import Base


class SupportMessageTypeEnum(str, enum.Enum):
    BUG = "bug"
    QUESTION = "question"
    SUGGESTION = "suggestion"


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    type = Column(
        Enum(SupportMessageTypeEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    message = Column(Text, nullable=False)
    screenshot_count = Column(Integer, nullable=False, default=0)
    # Diagnostics, captured at submit time. The first three come from the
    # ``x-client-*`` headers both clients already send for the client-usage
    # middleware, so the request body never repeats them; the last two are the
    # owner's own account settings. All of it rides in the email's
    # ``diagnostics.txt`` attachment rather than its body — Gmail quotes a body
    # on reply and drops attachments, and the sender must not read their own
    # owner id back out of our answer.
    client_app = Column(String, nullable=True)
    client_platform = Column(String, nullable=True)
    client_version = Column(String, nullable=True)
    language = Column(String(5), nullable=True)
    country = Column(String(2), nullable=True)
    email_sent_at = Column(DateTime, nullable=True)
    # Passed, not called, so the default is evaluated per insert and freezegun can
    # control it in tests. UTC, like every timestamp here; see ``app/clock.py``.
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

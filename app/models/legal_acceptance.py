"""Append-only record of a user accepting one version of one legal document.

Append-only on purpose. When a document is revised the owner accepts it again, and that
new row must not overwrite the evidence of what they agreed to before it — "which text
did this person accept, and when" is the only question this table exists to answer, and a
row that gets rewritten cannot answer it for any date but today. Repeated acceptances of
the *same* version are kept too rather than deduplicated: an affirmation is an event, and
collapsing two of them loses the fact that it happened twice (on two devices, say).

One row per document, not one per "the legal stuff": the Terms and the Privacy Policy are
separate documents with separate version constants, so revising one must not silently
invalidate — or falsely renew — the record for the other.

``locale`` matters as much as ``version``. The two documents exist in English and Hebrew
and a user agreed to one of them, not both; in a dispute, which text they read is the
question. ``version`` alone would not say.

What is deliberately absent is an IP address. It is the field this kind of table usually
carries, and it is real personal data; nothing else in this codebase stores one (Sentry is
given the Firebase uid and nothing else), and a timestamped, versioned, authenticated
acceptance is already the substance of the record.
"""

import enum

from sqlalchemy import Column, DateTime, Enum, Index, Integer, String

from app.clock import utc_now_naive
from app.models.base import Base
from app.models.device_token import DevicePlatformEnum


class LegalDocumentEnum(str, enum.Enum):
    TERMS = "terms"
    PRIVACY = "privacy"


class LegalAcceptance(Base):
    __tablename__ = "legal_acceptances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False)
    document = Column(
        Enum(LegalDocumentEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    # The version string the client actually displayed — never one the server assumed.
    # A client bundles its own document text, so only it knows what was on screen; a
    # server-supplied version would let an out-of-date build record consent to wording
    # the user was never shown.
    version = Column(String, nullable=False)
    # "en" or "he" — which of the two texts was on screen.
    locale = Column(String, nullable=False)
    # Reuses the push-notification platform enum rather than declaring a second one with
    # the same three members.
    platform = Column(
        Enum(DevicePlatformEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    accepted_at = Column(DateTime, nullable=False, default=utc_now_naive)

    __table_args__ = (
        Index("ix_legal_acceptances_owner_id", "owner_id"),
    )

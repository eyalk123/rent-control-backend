from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text

from app.models.base import Base


class Owner(Base):
    """Profile mirror of an authenticated owner, synced from the Firebase ID token.

    Firebase Auth remains the source of truth for authentication; this table exists so
    owner contact/profile info is queryable in SQL (emailing, admin tooling, analytics).
    ``id`` is the Firebase UID — the same value stored as ``owner_id`` on every other table.
    """

    __tablename__ = "owners"

    id = Column(String, primary_key=True)  # Firebase UID (== owner_id elsewhere)
    email = Column(String, nullable=True, index=True)
    display_name = Column(String, nullable=True)
    picture_url = Column(String, nullable=True)
    # Onboarding progress as a JSON document — see revision 045 for the shape and why it
    # lives on the account rather than in device storage. Two maps, deliberately apart:
    # `seeds_shown` records that a feature was *named* somewhere, `tours_seen` that it was
    # *explained*. Seeing the seed must never consume the destination tour.
    tour_state = Column(Text, nullable=False, server_default="{}", default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=True)

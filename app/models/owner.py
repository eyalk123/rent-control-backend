from datetime import datetime

from sqlalchemy import Column, DateTime, String

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
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=True)

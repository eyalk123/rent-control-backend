"""An append-only trace of destructive actions.

Deleting a property, renter or transaction is irreversible, and until now left nothing behind
to answer "what happened to it?". This records the fact of the deletion — not the row itself.

Deliberately a *trace, not a copy*: storing the whole record would be soft delete through the
back door, with the read-path risk (reports, the Excel export, the agent's read-only tools all
having to remember a filter) and none of the undo.

`label` holds a human-readable identifier — a renter's name, a property's address — so the log
is actually useful to read. That is personal data, so it is deleted with the account like
everything else (see `user_service.delete_account`).

The shape is generic (`action` + `entity_type`) so this can grow into the general activity log
the analytics work wants, rather than being redesigned. Only deletions are written today.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON, String

from app.models.base import Base


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)  # 'delete'
    entity_type = Column(String, nullable=False)  # property | renter | transaction
    entity_id = Column(Integer, nullable=False)
    label = Column(String, nullable=True)  # e.g. "רחוב הרצל 12" — personal data
    details = Column(JSON, nullable=True)  # small summary; never the full row
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

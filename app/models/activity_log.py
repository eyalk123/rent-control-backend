"""An append-only trace of what an owner did to their records.

Deleting a property, renter or transaction is irreversible, and until this existed left
nothing behind to answer "what happened to it?". This records the fact of the change — not
the row itself.

Deliberately a *trace, not a copy*: storing the whole record would be soft delete through the
back door, with the read-path risk (reports, the Excel export, the agent's read-only tools all
having to remember a filter) and none of the undo.

Actions written today: `delete` (property, renter, transaction), `terminate` and `reopen`
(renter), and `update` (property, renter, transaction). Creations are deliberately *not*
logged — a row's own `created_at` already says when it appeared, so a `create` action would
double the write volume to record something already known. Edits have no such record:
`updated_at` is overwritten in place and keeps only the most recent one.

`label` holds a human-readable identifier — a renter's name, a property's address — so the log
is actually useful to read. That is personal data, so it is deleted with the account like
everything else (see `user_service.delete_account`).

`details` is the opposite: **field names only, never values, old or new.** An `update` row
carries `{"fields": ["base_rent", "phone"]}`, which is what makes it useful, and never the
phone number itself — this table is read by analytics queries, and putting tenant contact
details in their path would be a quiet leak. See `app.services.activity_diff`.

The shape is generic (`action` + `entity_type`) so this can grow further without being
redesigned.
"""

from sqlalchemy import Column, DateTime, Integer, JSON, String

from app.clock import utc_now_naive
from app.models.base import Base


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)  # delete | update | terminate | reopen
    entity_type = Column(String, nullable=False)  # property | renter | transaction
    entity_id = Column(Integer, nullable=False)
    label = Column(String, nullable=True)  # e.g. "רחוב הרצל 12" — personal data
    details = Column(JSON, nullable=True)  # small summary; never the full row
    created_at = Column(DateTime, nullable=False, default=utc_now_naive, index=True)

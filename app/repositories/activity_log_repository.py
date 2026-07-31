"""Data access for the append-only activity log. Owner-scoped like everything else.

There is no read method here on purpose: nothing in the product surfaces the log yet, and the
analytics that motivated it query the table directly. Add one when a screen needs it.
"""
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog


class ActivityLogRepository:
    def __init__(self, session: Session):
        self.session = session

    def record_delete(
        self,
        owner_id: str,
        entity_type: str,
        entity_id: int,
        label: str | None = None,
        details: dict | None = None,
    ) -> ActivityLog:
        """Note that something was deleted. Caller commits — this is written in the same
        transaction as the delete it describes, so the two can't disagree."""
        entry = ActivityLog(
            owner_id=owner_id,
            action="delete",
            entity_type=entity_type,
            entity_id=entity_id,
            label=label,
            details=details,
        )
        self.session.add(entry)
        return entry

    def delete_owner_data(self, owner_id: str) -> None:
        """`label` holds names and addresses, so this goes when the account goes."""
        self.session.execute(delete(ActivityLog).where(ActivityLog.owner_id == owner_id))

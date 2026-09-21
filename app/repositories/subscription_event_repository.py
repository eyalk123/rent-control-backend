from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.subscription_event import SubscriptionEvent


class SubscriptionEventRepository:
    """The webhook audit log, and the idempotency check in front of it."""

    def __init__(self, session: Session):
        self.session = session

    def exists(self, event_id: str) -> bool:
        """Whether this event has already been recorded. Cheap — no row is loaded."""
        stmt = select(SubscriptionEvent.id).where(
            SubscriptionEvent.event_id == event_id
        ).limit(1)
        return self.session.scalar(stmt) is not None

    def record(
        self,
        *,
        event_id: str,
        event_type: str,
        owner_id: str | None,
        store: str | None,
        environment: str | None,
        occurred_at: datetime | None,
        applied: str,
        detail: str | None,
        payload: str,
    ) -> SubscriptionEvent:
        """Record an event, whatever was done with it.

        Events that were deliberately *not* applied are recorded too. "We never saw it"
        and "we saw it and decided it meant nothing" are different answers to a billing
        question, and only writing the first kind loses the distinction precisely when it
        is needed.
        """
        event = SubscriptionEvent(
            event_id=event_id,
            event_type=event_type,
            owner_id=owner_id,
            store=store,
            environment=environment,
            occurred_at=occurred_at,
            applied=applied,
            detail=detail,
            payload=payload,
        )
        self.session.add(event)
        self.session.commit()
        self.session.refresh(event)
        return event

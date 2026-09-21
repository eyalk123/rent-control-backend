from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from app.clock import utc_now_naive
from app.models.base import Base


class SubscriptionEvent(Base):
    """One row per webhook RevenueCat has delivered — the idempotency key and the audit trail.

    **Idempotency.** RevenueCat retries a delivery up to five times, and a retry is a fresh
    request carrying the same ``event_id``. Without a record of what has already been
    applied, a retry after a slow-but-successful response re-applies the event. ``event_id``
    is UNIQUE, so the second insert fails rather than the second application succeeding.

    **Audit.** Billing disputes are answered with evidence, not inference. ``payload`` keeps
    the raw JSON exactly as received, so "why did this account lose access on the 14th" has
    an answer that does not depend on reconstructing it from the current row.

    ``applied`` records whether the event actually changed entitlement. An event that was
    recorded but deliberately not applied — an unmapped product, an event type that carries
    no entitlement meaning, a payload that arrived out of order — is a different thing from
    one that was never seen, and the difference is what makes a quiet failure visible.
    """

    __tablename__ = "subscription_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    #: RevenueCat's own event id. UNIQUE — this is the idempotency guard.
    event_id = Column(String, nullable=False, unique=True, index=True)
    event_type = Column(String, nullable=False)
    #: The app_user_id the event names. Nullable: a malformed or identity-only event
    #: (SUBSCRIBER_ALIAS, TEST) may not name an owner, and it is still worth recording.
    owner_id = Column(String, nullable=True, index=True)
    store = Column(String, nullable=True)  # APP_STORE | PLAY_STORE | PADDLE | ...
    environment = Column(String, nullable=True)  # SANDBOX | PRODUCTION

    #: RevenueCat's event_timestamp_ms, as a datetime. Delivery order is not guaranteed,
    #: so this — never arrival time — is what decides whether an event is stale.
    occurred_at = Column(DateTime, nullable=True)

    applied = Column(String, nullable=False)  # applied | ignored | stale | unmapped
    #: Why, when not `applied`. Short and machine-ish, for log greps rather than users.
    detail = Column(String, nullable=True)

    payload = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

    __table_args__ = (
        Index("ix_subscription_events_owner_occurred", "owner_id", "occurred_at"),
    )

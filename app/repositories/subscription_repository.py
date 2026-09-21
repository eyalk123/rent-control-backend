from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.document_extraction_log import DocumentExtractionLog
from app.models.subscription import Subscription


class SubscriptionRepository:
    """Reads and writes the local billing record.

    ``upsert`` is the only write. Webhooks are the sole caller, they retry, and they can
    arrive out of order — so the write has to be a statement of current state rather than
    an edit, and it has to be safe to apply twice.
    """

    def __init__(self, session: Session):
        self.session = session

    def get_for_owner(self, owner_id: str) -> Subscription | None:
        return self.session.scalar(
            select(Subscription).where(Subscription.owner_id == owner_id)
        )

    def upsert(
        self,
        owner_id: str,
        *,
        plan: str,
        period: str,
        status: str,
        source: str,
        external_id: str | None = None,
        current_period_end: datetime | None = None,
        grace_until: datetime | None = None,
        price_amount: float | None = None,
        price_currency: str | None = None,
        last_event_at: datetime | None = None,
    ) -> Subscription:
        """Create or replace the owner's subscription state.

        Every field is overwritten, including with ``None``. A partial update would let a
        payload that omits ``grace_until`` leave a stale grace window in place, which is
        exactly the field that decides whether a lapsed subscriber still has access.

        Ordering is the caller's problem, not this method's: webhook delivery is not
        ordered, so the ingestion layer compares ``occurred_at`` against what is already
        stored and drops stale events before calling here.
        """
        subscription = self.get_for_owner(owner_id)
        if subscription is None:
            subscription = Subscription(owner_id=owner_id)
            self.session.add(subscription)

        subscription.plan = plan
        subscription.period = period
        subscription.status = status
        subscription.source = source
        subscription.external_id = external_id
        subscription.current_period_end = current_period_end
        subscription.grace_until = grace_until
        subscription.price_amount = price_amount
        subscription.price_currency = price_currency
        subscription.last_event_at = last_event_at

        self.session.commit()
        self.session.refresh(subscription)
        return subscription

    def count_lease_scans_since(self, owner_id: str, since: datetime) -> int:
        """Successful lease extractions for this owner since ``since``.

        Counts ``document_extraction_logs`` rather than keeping a counter, so the quota
        cannot drift from the thing it is counting, and so a refund or an investigation
        can always see which scans were charged against a month.

        Only ``success`` rows count. A scan that failed — an unreadable file, a provider
        timeout — cost the landlord nothing and must not consume one of three.
        """
        return (
            self.session.scalar(
                select(func.count())
                .select_from(DocumentExtractionLog)
                .where(
                    DocumentExtractionLog.owner_id == owner_id,
                    DocumentExtractionLog.created_at >= since,
                    DocumentExtractionLog.status == "success",
                )
            )
            or 0
        )

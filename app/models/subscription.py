from sqlalchemy import Column, DateTime, Integer, Numeric, String

from app.clock import utc_now_naive
from app.models.base import Base


class Subscription(Base):
    """One row per owner who has ever subscribed — the local record of billing state.

    **This table, not the payment provider, is what the product gates on.** RevenueCat and
    the stores are ingestion: webhooks land, this row is upserted, and every gate reads
    from here. A provider outage, a webhook backlog, or a later migration off RevenueCat
    must not change what a paying landlord can do — see the subscriptions plan §4.

    ``owner_id`` is unique on purpose. A landlord has one subscription; the constraint is
    what makes "subscribed on iOS *and* on the web" impossible to represent rather than
    merely discouraged, so double billing fails at write time instead of being discovered
    on a card statement. A move between rails updates this row's ``source``.

    Nothing here is authoritative about *money*. ``price_amount`` / ``price_currency`` are
    a copy of what the store reported for display and support, never something to bill or
    reconcile against: Apple and Google re-map their FX independently, so the same plan
    legitimately reads differently on two platforms.
    """

    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, unique=True, index=True)

    # Plan identifier as defined in app/services/entitlement_service.py. Stored rather
    # than derived because the plan a landlord *bought* and the property count they
    # currently hold are different facts: dropping to two properties does not silently
    # cancel a paid plan, and the difference is what the downgrade-at-renewal rule needs.
    plan = Column(String, nullable=False)
    period = Column(String, nullable=False)  # monthly | yearly

    # Lifecycle. Interpreted by entitlement_service.effective_plan, which is the only
    # place that decides what a status means — see its docstring for the rules.
    status = Column(String, nullable=False)  # active | trialing | grace | past_due | canceled | expired | paused

    # Which rail sold it: apple | google | paddle. Drives where the client sends someone
    # who wants to cancel — an App Store subscription cannot be cancelled by us, and
    # showing a cancel button that cannot work is worse than showing none.
    source = Column(String, nullable=False)
    # The provider's own identifier for this subscription (RevenueCat's, or the store's).
    # Nullable because a row can be written from a status refresh before any webhook has
    # named it.
    external_id = Column(String, nullable=True, index=True)

    # End of the period already paid for. Entitlement survives to this instant even after
    # cancellation, and it is the backstop that stops a missed renewal webhook granting a
    # plan forever.
    current_period_end = Column(DateTime, nullable=True)
    # How long a past_due subscription keeps working while dunning runs. Set from the
    # provider's grace period rather than invented here.
    grace_until = Column(DateTime, nullable=True)

    # Display only — see the class docstring.
    price_amount = Column(Numeric(10, 2), nullable=True)
    price_currency = Column(String(3), nullable=True)

    # The `event_timestamp_ms` of the last webhook applied to this row. Webhook delivery
    # is not ordered, so this is what lets a late RENEWAL be recognised as older than the
    # CANCELLATION already stored and discarded instead of moving the account backwards.
    last_event_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)

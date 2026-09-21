from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SubscriptionRead(BaseModel):
    """One account's plan, as both clients render it.

    Everything a paywall, a settings screen and a locked-property badge need, resolved
    server-side in one place. The alternative — clients deriving "am I over the limit"
    from a plan name and a property count — puts the band boundaries in three codebases
    and guarantees they disagree the first time a price changes.
    """

    #: Plan identifier: free | tier_3_8 | tier_9_15 | tier_16_plus.
    plan: str
    #: Inclusive property ceiling. `null` means unlimited, not zero.
    limit: Optional[int] = None
    property_count: int

    #: Properties over the ceiling: readable, not writable. Clients badge these.
    locked_property_ids: list[int] = []
    #: Whether to show the one-time explanation of why some properties are locked.
    #: Clients POST to /subscription/lock-notice/ack once it has been shown.
    show_lock_notice: bool = False

    #: False while the server is not enforcing limits. Clients should still show plan
    #: information, but must not present the account as restricted.
    enforced: bool

    # ── Billing, absent for an account that has never subscribed ──────────────
    #: apple | google | paddle — decides where a client sends someone who wants to
    #: cancel. An App Store subscription cannot be cancelled by us, and offering a
    #: button that cannot work is worse than offering none.
    source: Optional[str] = None
    status: Optional[str] = None
    period: Optional[str] = None
    current_period_end: Optional[datetime] = None

    #: What the store reported, for display only. Never used to bill or reconcile:
    #: Apple and Google re-map their own FX, so the same plan legitimately reads
    #: differently on two platforms.
    price_amount: Optional[float] = None
    price_currency: Optional[str] = None

    # ── Feature limits, so a client can gate its own UI without a second call ─
    monthly_lease_scans: Optional[int] = None
    lease_scans_used: int = 0
    agent: bool = False

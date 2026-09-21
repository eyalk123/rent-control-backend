"""The single accessor for what a subscription plan allows.

Every gate in the product reads a field on ``PlanLimits`` from here. Nothing else knows
that 8 is the top of the first paid band, that free accounts get three lease scans a
month, or that the assistant is a paid feature. Three reasons that matters:

**Prices and bands will move.** They are provisional today and will be tuned against real
conversion. A boundary written into ``property_service`` as ``if count > 8`` has to be
found again in six months, in a file whose job is properties.

**The store is not the source of truth.** Apple, Google and Paddle each report their own
product identifiers, and RevenueCat normalises them into an entitlement name. What that
entitlement *permits* is ours, and it must survive changing billing provider — see
``rent-control-subscriptions-plan.md`` §4.

**This is a second capability axis, not an extension of the first.**
``country_service.capabilities_for(country)`` answers "does this feature exist where the
property is"; this answers "does this account's plan include it". They are independent —
an Israeli account on the free plan and a French account on the top plan are different
questions — and merging them produces a matrix nobody can reason about. Keep them apart.

Plan resolution deliberately fails *open to free*, never to an error: a missing row, an
unrecognised plan name from a provider, or an expired subscription all resolve to the free
plan. A landlord locked out of their own ledger by a billing hiccup is a far worse outcome
than one who briefly gets more than they paid for.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.clock import utc_now_naive

# Plan identifiers. These strings are persisted on `subscriptions.plan` and are what the
# RevenueCat entitlement maps onto, so they are an external contract — renaming one is a
# migration, not an edit.
PLAN_FREE = "free"
PLAN_TIER_3_8 = "tier_3_8"
PLAN_TIER_9_15 = "tier_9_15"
PLAN_TIER_16_PLUS = "tier_16_plus"

# Subscription statuses that still confer the plan. `past_due` is handled separately
# because it depends on `grace_until` rather than being categorically in or out.
_ENTITLING_STATUSES = frozenset({"active", "trialing", "grace"})

UNLIMITED = None


@dataclass(frozen=True)
class PlanLimits:
    """What one plan permits. Every gate in the product reads a field on this."""

    plan: str
    #: Inclusive property-count band. `max_properties = None` means no ceiling.
    min_properties: int
    max_properties: int | None
    #: Successful AI lease extractions allowed per calendar month. `None` = unlimited.
    monthly_lease_scans: int | None
    #: Whether the portfolio chat assistant is included.
    agent: bool

    @property
    def is_free(self) -> bool:
        return self.plan == PLAN_FREE


# The bands, in ascending order. Boundaries are inclusive and must not overlap: an earlier
# draft had 8 in two bands and 15 in two more, which gives the gate two valid answers for
# one account. `_assert_contiguous` below makes that a startup failure rather than a bug
# someone finds in production.
#
# Paid plans are deliberately identical except for the property ceiling. The free plan is
# the only one that restricts features, and it is the restriction — not the price — that
# is the argument for upgrading at three properties.
PLANS: tuple[PlanLimits, ...] = (
    PlanLimits(
        plan=PLAN_FREE,
        min_properties=1,
        max_properties=2,
        monthly_lease_scans=3,
        agent=False,
    ),
    PlanLimits(
        plan=PLAN_TIER_3_8,
        min_properties=3,
        max_properties=8,
        monthly_lease_scans=UNLIMITED,
        agent=True,
    ),
    PlanLimits(
        plan=PLAN_TIER_9_15,
        min_properties=9,
        max_properties=15,
        monthly_lease_scans=UNLIMITED,
        agent=True,
    ),
    PlanLimits(
        plan=PLAN_TIER_16_PLUS,
        min_properties=16,
        max_properties=None,
        monthly_lease_scans=UNLIMITED,
        agent=True,
    ),
)

_BY_PLAN: dict[str, PlanLimits] = {p.plan: p for p in PLANS}


def _assert_contiguous() -> None:
    """The bands must tile the whole range with no gap and no overlap.

    Runs at import. A gap means some property count has no plan that permits it and the
    account can never become compliant; an overlap means two plans both claim a count and
    the "which plan do I need" answer depends on iteration order.
    """
    assert PLANS[0].min_properties == 1, "the first band must start at 1"
    assert PLANS[-1].max_properties is None, "the last band must be open-ended"
    for lower, upper in zip(PLANS, PLANS[1:]):
        assert lower.max_properties is not None, (
            f"only the last band may be open-ended; {lower.plan} is not last"
        )
        assert upper.min_properties == lower.max_properties + 1, (
            f"bands {lower.plan} and {upper.plan} are not contiguous: "
            f"{lower.max_properties} -> {upper.min_properties}"
        )


_assert_contiguous()


def limits_for(plan: str | None) -> PlanLimits:
    """The limits for a plan name. Never raises.

    An unknown name resolves to free rather than blowing up, because the name can arrive
    from a payment provider: a product created in a store console with a typo, or an
    entitlement renamed in RevenueCat, must not 500 every request the account makes.
    """
    if not plan:
        return _BY_PLAN[PLAN_FREE]
    return _BY_PLAN.get(plan.strip().lower(), _BY_PLAN[PLAN_FREE])


def required_plan_for(property_count: int) -> PlanLimits:
    """The cheapest plan that permits ``property_count`` properties.

    This is what a paywall shows: "you have 3 properties, you need this plan". Counts at
    or below zero resolve to free — an account with no properties is not mid-upgrade.
    """
    if property_count <= 0:
        return _BY_PLAN[PLAN_FREE]
    for band in PLANS:
        if band.max_properties is None or property_count <= band.max_properties:
            return band
    return PLANS[-1]  # unreachable while the last band is open-ended


def allows_property_count(plan: str | None, property_count: int) -> bool:
    """Whether ``plan`` permits holding this many properties."""
    limits = limits_for(plan)
    if limits.max_properties is None:
        return True
    return property_count <= limits.max_properties


def effective_plan(
    plan: str | None,
    status: str | None,
    current_period_end: datetime | None = None,
    grace_until: datetime | None = None,
    *,
    now: datetime | None = None,
) -> PlanLimits:
    """Resolve a stored subscription row into the plan that actually applies right now.

    Split out from the row itself so it can be reasoned about — and tested — without a
    database, and so the same rules apply whether the caller holds an ORM object or a
    webhook payload mid-ingestion.

    The rules, and why:

    - No subscription at all, or no plan name → free.
    - ``active`` / ``trialing`` / ``grace`` → the plan, provided the paid period has not
      already ended. The period check is a backstop: a renewal webhook that never arrives
      must not grant a plan indefinitely.
    - ``past_due`` → the plan while ``grace_until`` is in the future, free after. A failed
      card is usually an expired card, and dunning takes days; revoking on the first
      failed charge punishes the wrong thing.
    - ``canceled`` → the plan until ``current_period_end``, free after. They paid for the
      period; cancelling is a decision about the *next* one.
    - Anything else (``expired``, ``paused``, an unrecognised status) → free.
    """
    if not plan or not status:
        return _BY_PLAN[PLAN_FREE]

    limits = limits_for(plan)
    if limits.is_free:
        return limits

    moment = now or utc_now_naive()
    state = status.strip().lower()

    if state in _ENTITLING_STATUSES:
        if current_period_end is not None and current_period_end < moment:
            return _BY_PLAN[PLAN_FREE]
        return limits

    if state == "past_due":
        return limits if grace_until is not None and grace_until > moment else _BY_PLAN[PLAN_FREE]

    if state == "canceled":
        return (
            limits
            if current_period_end is not None and current_period_end > moment
            else _BY_PLAN[PLAN_FREE]
        )

    return _BY_PLAN[PLAN_FREE]

def better_plan(a: str | None, b: str | None) -> PlanLimits:
    """Whichever of two plans permits more.

    Exists because entitlement has two independent sources: a subscription someone pays
    for, and a permanent grant recorded on the account (`owners.granted_plan`, which is
    how the landlords who were here before billing existed keep their access for good).
    Neither can be allowed to cancel the other out. A grandfathered landlord who takes a
    small paid plan must not *lose* capacity for paying, and a lapsed subscription must
    not erase a grant that was never conditional on payment.
    """
    limits_a = limits_for(a)
    limits_b = limits_for(b)
    return limits_a if PLANS.index(limits_a) >= PLANS.index(limits_b) else limits_b


def split_by_allowance(
    ordered_property_ids: Sequence[int], limits: PlanLimits
) -> tuple[list[int], list[int]]:
    """Split properties into (writable, locked) for a plan.

    ``ordered_property_ids`` must be **oldest first**, and the caller owes that ordering a
    stable sort — it is the whole contract. The landlord keeps their earliest properties
    and loses write access to the excess.

    Deliberately not a random or "most recently active" choice. Random has no answer to
    "why this one?", and activity-based selection is *unstable*: editing one property
    would silently lock another, which is the kind of behaviour that generates support
    mail nobody can resolve. Oldest-first is stable, and it is explainable in one
    sentence on the paywall.

    When a picker is added later, it goes in front of this: use the explicit selection if
    the account has made one, and fall back here if not. Nothing in this function changes.
    """
    if limits.max_properties is None:
        return list(ordered_property_ids), []
    return (
        list(ordered_property_ids[: limits.max_properties]),
        list(ordered_property_ids[limits.max_properties :]),
    )

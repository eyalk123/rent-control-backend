"""Plan bands, feature limits, and how a stored subscription resolves into access.

The band boundaries are the point of this file. They are the one place where an
off-by-one is both easy to write and invisible until a landlord is either charged for a
plan they did not need or given one they did not buy — so 2/3, 8/9 and 15/16 are each
asserted from both sides rather than sampled.
"""
from datetime import datetime, timedelta

import pytest

from app.models.document_extraction_log import DocumentExtractionLog
from app.repositories.subscription_repository import SubscriptionRepository
from app.services import entitlement_service as ent
from tests.conftest import OWNER_A, OWNER_B

NOW = datetime(2026, 9, 21, 12, 0, 0)
LATER = NOW + timedelta(days=10)
EARLIER = NOW - timedelta(days=10)


# ── Bands ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "count,expected",
    [
        (0, ent.PLAN_FREE),
        (1, ent.PLAN_FREE),
        (2, ent.PLAN_FREE),
        (3, ent.PLAN_TIER_3_8),
        (8, ent.PLAN_TIER_3_8),
        (9, ent.PLAN_TIER_9_15),
        (15, ent.PLAN_TIER_9_15),
        (16, ent.PLAN_TIER_16_PLUS),
        (500, ent.PLAN_TIER_16_PLUS),
    ],
)
def test_required_plan_at_every_boundary(count, expected):
    assert ent.required_plan_for(count).plan == expected


def test_bands_tile_the_range_without_gap_or_overlap():
    """Every count from 1 to 40 resolves to exactly one band, and it is the band whose
    own range contains it. Guards the same invariant `_assert_contiguous` checks at
    import, but from the caller's side."""
    for count in range(1, 41):
        band = ent.required_plan_for(count)
        assert band.min_properties <= count
        assert band.max_properties is None or count <= band.max_properties


@pytest.mark.parametrize(
    "plan,count,allowed",
    [
        (ent.PLAN_FREE, 2, True),
        (ent.PLAN_FREE, 3, False),
        (ent.PLAN_TIER_3_8, 8, True),
        (ent.PLAN_TIER_3_8, 9, False),
        (ent.PLAN_TIER_9_15, 15, True),
        (ent.PLAN_TIER_9_15, 16, False),
        (ent.PLAN_TIER_16_PLUS, 10_000, True),
    ],
)
def test_allows_property_count(plan, count, allowed):
    assert ent.allows_property_count(plan, count) is allowed


# ── Feature limits ───────────────────────────────────────────────────────────


def test_free_plan_feature_limits():
    free = ent.limits_for(ent.PLAN_FREE)
    assert free.monthly_lease_scans == 3
    assert free.agent is False
    assert free.is_free is True


@pytest.mark.parametrize(
    "plan", [ent.PLAN_TIER_3_8, ent.PLAN_TIER_9_15, ent.PLAN_TIER_16_PLUS]
)
def test_paid_plans_are_feature_identical(plan):
    """Paid plans differ only by property ceiling. If this ever fails, the pricing page
    is lying — it tells visitors every paid plan includes the same thing."""
    limits = ent.limits_for(plan)
    assert limits.monthly_lease_scans is ent.UNLIMITED
    assert limits.agent is True


def test_unknown_plan_name_resolves_to_free_not_an_error():
    """A provider can hand us a product id nobody recognises — a typo in a store console,
    a renamed RevenueCat entitlement. That must degrade, not 500 every request."""
    assert ent.limits_for("tier_enterprise_xl").plan == ent.PLAN_FREE
    assert ent.limits_for(None).plan == ent.PLAN_FREE
    assert ent.limits_for("").plan == ent.PLAN_FREE


def test_plan_name_matching_is_forgiving_of_case_and_padding():
    assert ent.limits_for("  TIER_3_8 ").plan == ent.PLAN_TIER_3_8


# ── Resolving a stored subscription ──────────────────────────────────────────


@pytest.mark.parametrize("status", ["active", "trialing", "grace"])
def test_entitling_statuses_grant_the_plan(status):
    resolved = ent.effective_plan(
        ent.PLAN_TIER_9_15, status, current_period_end=LATER, now=NOW
    )
    assert resolved.plan == ent.PLAN_TIER_9_15


def test_active_but_period_already_ended_falls_back_to_free():
    """The backstop against a renewal webhook that never arrives: an 'active' row whose
    paid period ended two weeks ago is stale data, not an entitlement."""
    resolved = ent.effective_plan(
        ent.PLAN_TIER_9_15, "active", current_period_end=EARLIER, now=NOW
    )
    assert resolved.plan == ent.PLAN_FREE


def test_past_due_keeps_access_while_in_grace_and_loses_it_after():
    inside = ent.effective_plan(
        ent.PLAN_TIER_3_8, "past_due", grace_until=LATER, now=NOW
    )
    assert inside.plan == ent.PLAN_TIER_3_8

    outside = ent.effective_plan(
        ent.PLAN_TIER_3_8, "past_due", grace_until=EARLIER, now=NOW
    )
    assert outside.plan == ent.PLAN_FREE

    never_set = ent.effective_plan(ent.PLAN_TIER_3_8, "past_due", now=NOW)
    assert never_set.plan == ent.PLAN_FREE


def test_canceled_keeps_access_until_the_period_they_paid_for_ends():
    still_paid = ent.effective_plan(
        ent.PLAN_TIER_16_PLUS, "canceled", current_period_end=LATER, now=NOW
    )
    assert still_paid.plan == ent.PLAN_TIER_16_PLUS

    lapsed = ent.effective_plan(
        ent.PLAN_TIER_16_PLUS, "canceled", current_period_end=EARLIER, now=NOW
    )
    assert lapsed.plan == ent.PLAN_FREE


@pytest.mark.parametrize("status", ["expired", "paused", "refunded", "who_knows"])
def test_non_entitling_statuses_fall_back_to_free(status):
    resolved = ent.effective_plan(
        ent.PLAN_TIER_3_8, status, current_period_end=LATER, now=NOW
    )
    assert resolved.plan == ent.PLAN_FREE


def test_no_subscription_at_all_is_the_free_plan():
    assert ent.effective_plan(None, None, now=NOW).plan == ent.PLAN_FREE
    assert ent.effective_plan(ent.PLAN_TIER_3_8, None, now=NOW).plan == ent.PLAN_FREE


# ── Repository ───────────────────────────────────────────────────────────────


def test_upsert_creates_then_replaces_rather_than_duplicating(db_session):
    """Webhooks retry. Applying the same event twice must leave one row, and the unique
    constraint on owner_id means a second row is not merely untidy — it fails."""
    repo = SubscriptionRepository(db_session)

    first = repo.upsert(
        OWNER_A,
        plan=ent.PLAN_TIER_3_8,
        period="monthly",
        status="active",
        source="paddle",
        external_id="sub_1",
        current_period_end=LATER,
    )
    second = repo.upsert(
        OWNER_A,
        plan=ent.PLAN_TIER_9_15,
        period="yearly",
        status="active",
        source="paddle",
        external_id="sub_1",
        current_period_end=LATER,
    )

    assert first.id == second.id
    assert second.plan == ent.PLAN_TIER_9_15
    assert second.period == "yearly"
    assert repo.get_for_owner(OWNER_A).plan == ent.PLAN_TIER_9_15


def test_upsert_clears_fields_the_new_state_omits(db_session):
    """A partial update would leave a stale grace window behind — and grace_until is
    precisely the field that decides whether a lapsed subscriber still has access."""
    repo = SubscriptionRepository(db_session)
    repo.upsert(
        OWNER_A,
        plan=ent.PLAN_TIER_3_8,
        period="monthly",
        status="past_due",
        source="apple",
        grace_until=LATER,
    )
    recovered = repo.upsert(
        OWNER_A,
        plan=ent.PLAN_TIER_3_8,
        period="monthly",
        status="active",
        source="apple",
        current_period_end=LATER,
    )
    assert recovered.grace_until is None


def test_subscriptions_are_owner_scoped(db_session):
    repo = SubscriptionRepository(db_session)
    repo.upsert(
        OWNER_A, plan=ent.PLAN_TIER_3_8, period="monthly", status="active", source="paddle"
    )
    assert repo.get_for_owner(OWNER_B) is None


def _scan(db_session, owner_id, status, created_at):
    db_session.add(
        DocumentExtractionLog(
            owner_id=owner_id, status=status, created_at=created_at
        )
    )
    db_session.commit()


def test_scan_count_includes_only_this_owner_this_period_and_only_successes(db_session):
    repo = SubscriptionRepository(db_session)
    window_start = NOW - timedelta(days=21)

    _scan(db_session, OWNER_A, "success", NOW - timedelta(days=1))
    _scan(db_session, OWNER_A, "success", NOW - timedelta(days=2))
    # A scan that failed cost the landlord nothing and must not burn one of three.
    _scan(db_session, OWNER_A, "error", NOW - timedelta(days=3))
    _scan(db_session, OWNER_A, "unsupported", NOW - timedelta(days=3))
    # Before the window.
    _scan(db_session, OWNER_A, "success", NOW - timedelta(days=40))
    # Another owner entirely.
    _scan(db_session, OWNER_B, "success", NOW - timedelta(days=1))

    assert repo.count_lease_scans_since(OWNER_A, window_start) == 2


def test_scan_count_is_zero_for_an_owner_who_has_never_scanned(db_session):
    repo = SubscriptionRepository(db_session)
    assert repo.count_lease_scans_since(OWNER_A, NOW - timedelta(days=30)) == 0

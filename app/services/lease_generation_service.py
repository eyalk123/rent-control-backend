"""Keeps an open-ended lease's schedule ahead of the calendar.

A tenancy with no agreed end is the norm across most of Europe, and happens in Israel
whenever a tenant holds over month to month. The product cannot store "no end date": every
date derives from a lease start plus a list of periods, and `renter_repository`'s
``effective_lease_end()`` is ``coalesce(terminated_on, lease_end)`` — a NULL there evaluates
to NULL in SQL, so a null-ended renter would silently drop out of `get_active`,
`get_overdue_this_month` and `get_by_property_id` alike.

So an open-ended lease does not have *no* end. It has a **rolling horizon**: five periods
from the current one onward, topped up by one whenever an anniversary passes. Every consumer
of the year list keeps reading a real date, and the only thing that changes is that the date
moves. That is the whole trick, and it is why this feature costs a job and a boolean rather
than a rewrite of every screen that reads a lease.

**What it never does is reprice.** It appends, and only ever at the far end. An amount the
owner has corrected by hand is upstream of everything this writes, and the new period is
priced by *chaining* off the last existing one — so a correction propagates forward on its
own, and a period that has already started is never touched. The contrast with
`cpi_indexing_service`, which recomputes existing periods against a published index and needs
`is_frozen` to know when to stop, is deliberate: nothing here can rewrite history, so nothing
here needs a freeze.

**An index-linked lease is topped up here and priced there.** This file owns no index cache
and is not going to grow one — it is pure arithmetic, which is what makes it testable without
a database. So a `cpi` lease gets its new period appended at the previous period's amount, as
a placeholder, and `run-cpi-indexing` resolves it against the index within the hour: the
scheduler runs this at 02:00 UTC and indexing at 03:00, and `run-reminders` performs the two
catch-ups in that same order. A placeholder that survives to a screen is the previous year's
rent, which is also what an unpublished index month falls back to.
"""
import json
import logging
from datetime import date
from typing import Optional

from dateutil.relativedelta import relativedelta

from app.repositories.renter_repository import RenterRepository
from app.services import lease_periods
from app.services.cpi_indexing_service import apply_year_rule

logger = logging.getLogger(__name__)

#: How many periods to keep on the lease, counting the one it is in. Five is long enough that
#: the schedule looks like a real agreement and short enough that a twenty-year tenancy does
#: not accumulate an unreadable timeline. It is a horizon, not a term: nothing about the lease
#: ends at the fifth one.
HORIZON_PERIODS = 5

#: Modes this job can extend. Everything but `custom`, which gives each period its own rule
#: and so cannot describe a period that does not exist yet — which is why the API refuses to
#: pair it with the switch (`renter_service._guard_open_ended_mode`). Listed here as well so a
#: row that predates that guard is skipped rather than mispriced.
#:
#: `cpi` belongs here: whole-lease index linkage prices every period from the base frozen at
#: signing, and never asks where the schedule ends. See the placeholder note in the module
#: docstring for how its appended periods get their real amount.
GENERATABLE_MODES = {None, "none", "percent", "fixed", "cpi"}


def remaining_periods(lease_start: date, lease_years: list[dict], today: date) -> int:
    """How many periods have not finished yet, including the one in progress.

    Counting from the *current* period rather than the next one is what makes the horizon
    stable: a lease created today with five periods is already correct and must not be topped
    up on its first night, or every new lease would immediately grow a sixth year.
    """
    remaining = 0
    for index, year in enumerate(lease_years):
        start = lease_periods.period_start(lease_start, lease_years, index)
        end = start + relativedelta(months=lease_periods.period_months(year))
        if end > today:
            remaining += 1
    return remaining


def extend_to_horizon(
    lease_years: list[dict],
    lease_start: date,
    mode: Optional[str],
    escalation_value: Optional[float],
    today: date,
    horizon: int = HORIZON_PERIODS,
) -> list[dict]:
    """Return ``lease_years`` with enough periods appended to restore the horizon.

    Pure: no session, no renter, so the arithmetic is testable on its own. Returns the input
    list unchanged (same object) when nothing is needed, which is what lets the caller skip
    the write entirely and keeps the job idempotent.
    """
    if not lease_years or not lease_start:
        return lease_years
    missing = horizon - remaining_periods(lease_start, lease_years, today)
    if missing <= 0:
        return lease_years

    # `cpi` is deliberately not handed to `apply_year_rule` as a rule: whole-lease index
    # linkage is fixed-base, not chained, so the rule engine would answer the wrong question
    # even though it happens to return the same placeholder. Saying "hold at the previous
    # amount until indexing runs" outright is what the code actually means.
    rule = {"mode": mode or "none", "value": escalation_value or 0}
    indexed = mode == "cpi"
    extended = list(lease_years)
    for _ in range(missing):
        previous = extended[-1]["amount"]
        extended.append(
            {
                # Priced by chaining off the period before it, never as `base * (1 + r) ** n`.
                # The difference only shows once an amount has been corrected by hand — and
                # then it is the whole point, because chaining carries the correction forward
                # and the closed form silently discards it.
                "amount": (
                    round(previous)
                    if indexed
                    else apply_year_rule(previous, previous, rule, None, None)
                ),
                # `contract`, not `option`. An option period is one the tenant may decline,
                # and the app asks the owner to decide about it; there is no such decision
                # here. It also keeps `contract_end` moving with the horizon, which is what
                # stops the expiry countdown ever coming into range.
                "type": "contract",
                # What tells the clients to badge this period as automatic, and what a later
                # job would read to know which periods are its own rather than the owner's.
                "generated": True,
            }
        )
    return extended


class LeaseGenerationService:
    """The nightly top-up. One pass over every live open-ended lease."""

    def __init__(self, renter_repository: RenterRepository):
        self.renter_repository = renter_repository

    def run_lease_generation(self, today: Optional[date] = None) -> dict:
        today = today or date.today()
        summary = {"renters_examined": 0, "renters_extended": 0, "periods_added": 0}

        # Terminated leases are excluded by the query itself, not skipped here — the same
        # placement, and the same reasoning, as the CPI job's candidate set.
        for renter in self.renter_repository.get_open_ended():
            summary["renters_examined"] += 1
            if not renter.lease_start:
                continue
            if renter.rent_escalation_mode not in GENERATABLE_MODES:
                continue
            try:
                current = json.loads(renter.lease_years) if renter.lease_years else []
            except (json.JSONDecodeError, TypeError):
                # A malformed blob is the CPI job's rule too: leave it alone rather than
                # replace it with a guess about what it used to say.
                logger.warning("renter %s has unreadable lease_years; skipped", renter.id)
                continue
            if not current:
                continue

            extended = extend_to_horizon(
                current,
                renter.lease_start,
                renter.rent_escalation_mode,
                renter.rent_escalation_value,
                today,
            )
            if extended is current:
                continue

            added = len(extended) - len(current)
            self.renter_repository.update(
                renter,
                {
                    "lease_years": json.dumps(extended),
                    "lease_end": lease_periods.schedule_end(renter.lease_start, extended),
                    "contract_end": lease_periods.contract_end(renter.lease_start, extended),
                },
            )
            summary["renters_extended"] += 1
            summary["periods_added"] += added

        return summary

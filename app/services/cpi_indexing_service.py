"""CPI (Consumer Price Index) rent linkage.

For renters whose ``rent_escalation_mode == 'cpi'`` the rent is linked to the
general CPI (CBS series 120010). Unlike percent/fixed escalation — which the
client materializes up front — CPI amounts depend on index values that aren't
known at signing, so the **backend owns them**:

- A *base index* is frozen at signing = the known index at ``lease_start``.
- Each lease year N's rent = ``round(base_rent * max(known_index_N / base_index, 1))``
  where ``known_index_N`` is the index known at ``lease_start + N years``. The
  ``max(.., 1)`` floors rent at the original ``base_rent`` (never decreases below
  it, matching the standard "לא יפחת" clause).
- Years whose index isn't published yet stay at ``base_rent`` as a projection and
  are filled in by the indexing job once the reading exists.

The pure helpers below are the single source of truth for the math (used by both
renter create/update and the job) and are unit-testable with a fake ``index_lookup``.
"""
import json
import logging
from datetime import date
from typing import Callable, Optional, Sequence

from dateutil.relativedelta import relativedelta

from app.config import settings
from app.repositories.cpi_index_repository import CpiIndexRepository, reference_period
from app.repositories.renter_repository import RenterRepository
from app.services.index_source import IndexSource

logger = logging.getLogger(__name__)

IndexLookup = Callable[[date], Optional[float]]


def _period_str(period: Optional[tuple[int, int]]) -> Optional[str]:
    return f"{period[0]:04d}-{period[1]:02d}" if period else None


def compute_cpi_amount(
    base_rent: float, base_index: Optional[float], known_index: Optional[float]
) -> float:
    """Rent for one lease year under fixed-base-index linkage with a floor at
    ``base_rent``. Falls back to ``base_rent`` (a projection) whenever the base or
    the year's index isn't available yet."""
    if not base_index or base_index <= 0 or not known_index:
        return round(base_rent)
    ratio = max(known_index / base_index, 1.0)
    return round(base_rent * ratio)


def materialize_cpi_amounts(
    lease_years: list[dict],
    lease_start: date,
    base_rent: float,
    base_index: Optional[float],
    index_lookup: IndexLookup,
) -> list[dict]:
    """Return a copy of ``lease_years`` with each year's ``amount`` recomputed from
    the CPI linkage. The contract/option ``type`` split and the number of years are
    taken from the input untouched — only amounts change."""
    result: list[dict] = []
    for i, year in enumerate(lease_years):
        anniversary = lease_start + relativedelta(years=i)
        known_index = index_lookup(anniversary)
        result.append(
            {
                "amount": compute_cpi_amount(base_rent, base_index, known_index),
                "type": year["type"],
            }
        )
    return result


# --- Per-year rules ("custom" mode) -----------------------------------------------
#
# Under ``rent_escalation_mode == 'custom'`` each year past the first carries its own
# ``rule`` and derives from the *previous year's resolved amount*. That makes the
# schedule a forward walk, which is what lets a percent year sit after a CPI year: by
# the time the walk reaches it, the CPI year's real amount is already known.
#
# Note the CPI rule here is *chained* — it indexes off the previous year's amount and
# the index movement over that one year — whereas the whole-lease `cpi` mode above is
# fixed-base (everything against ``cpi_base_index`` frozen at signing). Chained is the
# only model that composes, because a CPI year has to hand a concrete amount to
# whatever rule follows it. The two are kept deliberately separate; `cpi` leases are
# untouched by any of this.


def _rule_of(year: dict) -> dict:
    rule = year.get("rule")
    return rule if isinstance(rule, dict) else {}


def has_cpi_rule(lease_years: list[dict]) -> bool:
    """True when any year is CPI-linked — i.e. the indexing job has work to do on this
    lease even though its mode is ``custom``, not ``cpi``."""
    return any(_rule_of(y).get("mode") == "cpi" for y in lease_years)


def compute_chained_cpi_amount(
    prev_amount: float, prev_index: Optional[float], known_index: Optional[float]
) -> float:
    """One CPI-linked year under chained linkage, floored at the previous year's rent
    (the "לא יפחת" clause). Falls back to ``prev_amount`` — a flat projection — whenever
    either anniversary's index isn't published yet; the indexing job fills it in later."""
    if not prev_index or prev_index <= 0 or not known_index:
        return round(prev_amount)
    ratio = max(known_index / prev_index, 1.0)
    return round(prev_amount * ratio)


def apply_year_rule(
    prev_amount: float,
    current_amount: float,
    rule: dict,
    prev_index: Optional[float],
    known_index: Optional[float],
) -> float:
    """Rent for one lease year from its rule and the previous year's resolved amount.
    ``manual`` (and an absent rule, which is what every legacy year has) keeps the
    amount the owner typed."""
    mode = rule.get("mode") or "manual"
    value = rule.get("value") or 0
    if mode == "none":
        return round(prev_amount)
    if mode == "percent":
        return round(prev_amount * (1 + value / 100))
    if mode == "fixed":
        return round(prev_amount + value)
    if mode == "cpi":
        return compute_chained_cpi_amount(prev_amount, prev_index, known_index)
    return round(current_amount)  # manual


def materialize_ruled_lease_years(
    lease_years: list[dict],
    lease_start: Optional[date],
    base_rent: Optional[float],
    index_lookup: IndexLookup,
) -> list[dict]:
    """Walk a ``custom`` lease forward, resolving each year's amount from its rule. Year
    one is always the base rent. ``type`` and ``rule`` pass through untouched — only
    amounts change. Without a ``lease_start`` no anniversary is knowable, so CPI years
    project flat and the rest still compute normally."""
    if not lease_years:
        return []

    result: list[dict] = []
    prev_amount = base_rent if base_rent else lease_years[0].get("amount", 0)
    for i, year in enumerate(lease_years):
        out = dict(year)
        if i == 0:
            out["amount"] = round(prev_amount)
        else:
            prev_index = known_index = None
            if lease_start is not None:
                prev_index = index_lookup(lease_start + relativedelta(years=i - 1))
                known_index = index_lookup(lease_start + relativedelta(years=i))
            out["amount"] = apply_year_rule(
                prev_amount, year.get("amount", 0), _rule_of(year), prev_index, known_index
            )
        prev_amount = out["amount"]
        result.append(out)
    return result


class CpiIndexingService:
    """The daily job: refresh the cached index, then recompute every CPI-linked renter's
    stored ``lease_years`` so newly-published readings take effect. Silent towards owners —
    no notifications (per product decision).

    Not silent towards *operators*, though. The fetch is best-effort by design, so a run
    that reached no source still succeeds structurally; what makes a real outage visible is
    the staleness check, which compares the newest cached month against the month that
    ought to be published by now. See :meth:`run_cpi_indexing`.
    """

    def __init__(
        self,
        renter_repository: RenterRepository,
        cpi_index_repository: CpiIndexRepository,
        sources: Sequence[IndexSource],
        index_id: int | None = None,
        max_stale_months: int | None = None,
    ):
        self.renter_repository = renter_repository
        self.cpi_index_repository = cpi_index_repository
        # Ordered by authority: the first source that returns rows wins, and the rest are
        # never called. sources[0] is the one whose absence counts as "degraded".
        self.sources = list(sources)
        self.index_id = index_id if index_id is not None else settings.CPI_INDEX_ID
        self.max_stale_months = (
            max_stale_months
            if max_stale_months is not None
            else settings.CPI_MAX_STALE_MONTHS
        )

    def _lookup(self) -> IndexLookup:
        return lambda d: self.cpi_index_repository.latest_on_or_before(self.index_id, d)

    def _refresh_cache(self, summary: dict) -> set[tuple[int, int]]:
        """Try each source in order until one yields rows. Returns the set of periods
        whose source was upgraded, so the caller knows to re-derive frozen values."""
        full_backfill = self.cpi_index_repository.is_empty(self.index_id)
        superseded: set[tuple[int, int]] = set()
        for source in self.sources:
            rows = source.fetch_all() if full_backfill else source.fetch_latest()
            if not rows:
                continue
            summary["fetched"], superseded = self.cpi_index_repository.upsert_many(
                self.index_id, rows, source=source.name
            )
            summary["source"] = source.name
            break

        served_by = summary["source"]
        if served_by is None:
            logger.warning(
                "CPI: no index source returned data (tried %s)",
                ", ".join(s.name for s in self.sources) or "none",
            )
        elif self.sources and served_by != self.sources[0].name:
            # Correct data from a fallback is a working state, not a failure — the run
            # still reports success. Staleness is what escalates.
            summary["degraded"] = True
            logger.warning(
                "CPI: %s unavailable, served from %s", self.sources[0].name, served_by
            )
        return superseded

    def _staleness(self, summary: dict) -> None:
        """How far the cache trails the newest month that should be published by now."""
        expected = reference_period(date.today())
        latest = self.cpi_index_repository.latest_period(self.index_id)
        summary["expected_period"] = _period_str(expected)
        summary["latest_period"] = _period_str(latest)
        if latest is None:
            # An empty cache is maximally stale: every CPI lease is stuck at base rent.
            summary["stale_months"] = None
            summary["stale"] = True
            return
        stale_months = (expected[0] * 12 + expected[1]) - (latest[0] * 12 + latest[1])
        summary["stale_months"] = max(stale_months, 0)
        summary["stale"] = summary["stale_months"] > self.max_stale_months

    def run_cpi_indexing(self) -> dict:
        summary = {
            "fetched": 0,
            "renters_updated": 0,
            "renters_before_index_start": 0,
            "source": None,
            "degraded": False,
            "stale": False,
        }

        # 1. Refresh the cache from the first source that answers: full backfill when
        #    empty, else the latest few months.
        superseded = self._refresh_cache(summary)
        # A period changing hands means anything frozen against the old value is now
        # anchored to a superseded reading, so re-derive the frozen bases this run.
        reconcile_bases = bool(superseded)

        # 2. Recompute every CPI-linked renter against the (now fresher) cache. Two kinds
        #    qualify: whole-lease `cpi` leases, and `custom` leases with at least one
        #    CPI year — the latter still need the index even though their other years
        #    are percent/fixed/manual.
        lookup = self._lookup()
        for renter in self.renter_repository.get_by_escalation_modes(["cpi", "custom"]):
            if not renter.lease_start:
                continue
            try:
                current = json.loads(renter.lease_years) if renter.lease_years else []
            except (json.JSONDecodeError, TypeError):
                continue
            if not current:
                continue

            base_rent = renter.base_rent or current[0]["amount"]

            if renter.rent_escalation_mode == "custom":
                if not has_cpi_rule(current):
                    continue  # no index dependency; its amounts are already final
                # Chained linkage needs no frozen base — every anniversary is looked up.
                base_index = renter.cpi_base_index
                new_years = materialize_ruled_lease_years(
                    current, renter.lease_start, base_rent, lookup
                )
            else:
                # Freeze the base index if it wasn't resolvable at signing (e.g. created
                # while the cache was empty); otherwise keep it fixed for the contract.
                # ``reconcile_bases`` is the one exception: a base frozen against a
                # provisional reading has to be re-anchored once the authoritative source
                # supersedes it, or it would stay on the fallback's value forever.
                base_index = renter.cpi_base_index
                if base_index is None or reconcile_bases:
                    base_index = lookup(renter.lease_start)
                if base_index is None:
                    # No reading at or before this lease's start: it predates the cached
                    # history, so its rent silently holds at base_rent. Counted so a lease
                    # older than HISTORY_FLOOR shows up instead of quietly undercharging.
                    summary["renters_before_index_start"] += 1
                new_years = materialize_cpi_amounts(
                    current, renter.lease_start, base_rent, base_index, lookup
                )

            if new_years != current or renter.cpi_base_index != base_index:
                self.renter_repository.update(
                    renter,
                    {"lease_years": json.dumps(new_years), "cpi_base_index": base_index},
                )
                summary["renters_updated"] += 1

        # 3. Report. A run that fetched nothing is only a problem if the cache has fallen
        #    behind — which is the check that would have caught the CBS outage.
        self._staleness(summary)
        if summary["renters_before_index_start"]:
            # Not stale — the cache is current, just doesn't reach far enough back. Lowering
            # HISTORY_FLOOR and letting the next run re-backfill is the fix.
            logger.warning(
                "CPI: %s CPI-linked lease(s) start before the cached history (from %s) and "
                "are holding at base rent — consider lowering HISTORY_FLOOR",
                summary["renters_before_index_start"],
                _period_str(self.cpi_index_repository.earliest_period(self.index_id)),
            )
        if summary["stale"]:
            logger.error(
                "CPI indexing: cache is stale (have %s, expected %s) — %s",
                summary["latest_period"] or "nothing",
                summary["expected_period"],
                summary,
            )
        else:
            logger.info("CPI indexing: %s", summary)
        return summary

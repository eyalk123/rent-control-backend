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
- Once a year has *started* and resolved against its correct known-index month, its
  amount is **frozen** — see :func:`is_frozen`.

The pure helpers below are the single source of truth for the math (used by both
renter create/update and the job) and are unit-testable with a fake ``index_lookup``.
"""
import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional, Sequence

from dateutil.relativedelta import relativedelta

from app.config import settings
from app.models.notification import NotificationTypeEnum
from app.models.notification_settings import (
    DEFAULT_CPI_MIN_CHANGE_AMOUNT,
    DEFAULT_CPI_MIN_CHANGE_PERCENT,
)
from app.repositories.cpi_index_repository import CpiIndexRepository, reference_period
from app.repositories.notification_repository import NotificationRepository
from app.repositories.notification_settings_repository import (
    NotificationSettingsRepository,
)
from app.repositories.renter_repository import RenterRepository
from app.services import cpi_rent_change as cpi
from app.services.index_source import IndexSource

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexReading:
    """One cached CPI reading: which month it is for, and its value.

    The *month* matters as much as the value. Amounts used to be derived from a bare
    float, which made it impossible to tell "resolved against the right month" from
    "resolved against an older month because the right one wasn't cached yet" — and
    the two behave differently under :func:`is_frozen`.
    """

    year: int
    month: int
    value: float

    def as_dict(self) -> dict:
        return {"year": self.year, "month": self.month, "value": self.value}


# Resolves the CPI reading a given anniversary should use, or ``None`` when nothing
# at or before it is cached yet.
IndexLookup = Callable[[date], Optional[IndexReading]]


def _value_of(reading: Optional[IndexReading]) -> Optional[float]:
    return reading.value if reading is not None else None


def is_frozen(year: dict, anniversary: date, today: date) -> bool:
    """Whether a lease year's amount is settled and must not be recomputed.

    Two conditions, both required:

    1. **The year has started.** A future year is a projection; it keeps tracking the
       cache until its anniversary arrives.
    2. **It resolved against its own known-index month.** ``latest_on_or_before``
       returns the newest reading *on or before* the reference month, so when neither
       feed was answering a started year can end up anchored to an older month. That is
       a known-wrong number, and freezing it would make it permanent — so it stays
       unfrozen and keeps self-healing until the correct month lands.

    A year with no ``cpi_reading`` never freezes, which is what keeps deterministic
    (percent / fixed / manual) years in a ``custom`` lease recomputing normally.
    """
    if anniversary > today:
        return False
    reading = year.get("cpi_reading")
    if not isinstance(reading, dict):
        return False
    return (reading.get("year"), reading.get("month")) == reference_period(anniversary)


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
    today: Optional[date] = None,
    force_recompute: bool = False,
) -> list[dict]:
    """Return a copy of ``lease_years`` with each year's ``amount`` recomputed from
    the CPI linkage. The contract/option ``type`` split and the number of years are
    taken from the input untouched — only amounts change.

    Years that :func:`is_frozen` pass through with their amount and reading intact, so
    ordinary index noise can no longer move rent the tenant has already started paying.

    ``force_recompute`` overrides that for one run. It is set when an authoritative
    source has superseded a reading a provisional value was derived from: the freeze
    exists to stop *correct* numbers drifting, not to make a *wrong* one permanent."""
    today = today or date.today()
    result: list[dict] = []
    for i, year in enumerate(lease_years):
        anniversary = lease_start + relativedelta(years=i)
        if not force_recompute and is_frozen(year, anniversary, today):
            result.append(
                {
                    "amount": year["amount"],
                    "type": year["type"],
                    "cpi_reading": year["cpi_reading"],
                }
            )
            continue
        reading = index_lookup(anniversary)
        out = {
            "amount": compute_cpi_amount(base_rent, base_index, _value_of(reading)),
            "type": year["type"],
        }
        if reading is not None:
            out["cpi_reading"] = reading.as_dict()
        result.append(out)
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
    today: Optional[date] = None,
    force_recompute: bool = False,
) -> list[dict]:
    """Walk a ``custom`` lease forward, resolving each year's amount from its rule. Year
    one is always the base rent. ``type`` and ``rule`` pass through untouched — only
    amounts change. Without a ``lease_start`` no anniversary is knowable, so CPI years
    project flat and the rest still compute normally.

    A frozen CPI year (see :func:`is_frozen`) keeps its amount and still hands it to the
    walk, so the deterministic years after it continue to derive normally."""
    if not lease_years:
        return []

    today = today or date.today()
    result: list[dict] = []
    prev_amount = base_rent if base_rent else lease_years[0].get("amount", 0)
    for i, year in enumerate(lease_years):
        out = dict(year)
        anniversary = lease_start + relativedelta(years=i) if lease_start else None
        if i == 0:
            out["amount"] = round(prev_amount)
        elif (
            not force_recompute
            and anniversary is not None
            and is_frozen(year, anniversary, today)
        ):
            out["amount"] = year["amount"]
        else:
            prev_reading = reading = None
            if lease_start is not None:
                prev_reading = index_lookup(lease_start + relativedelta(years=i - 1))
                reading = index_lookup(anniversary)
            out["amount"] = apply_year_rule(
                prev_amount,
                year.get("amount", 0),
                _rule_of(year),
                _value_of(prev_reading),
                _value_of(reading),
            )
            # Only a CPI year carries a reading — which is exactly what makes the
            # deterministic modes ineligible for freezing.
            if _rule_of(year).get("mode") == "cpi" and reading is not None:
                out["cpi_reading"] = reading.as_dict()
            else:
                out.pop("cpi_reading", None)
        prev_amount = out["amount"]
        result.append(out)
    return result


class CpiIndexingService:
    """The daily job: refresh the cached index, then recompute every CPI-linked renter's
    stored ``lease_years`` so newly-published readings take effect.

    It also raises the **confirmation** half of the ``cpi_rent_change`` notification. That
    has to happen here and nowhere else: the rules engine derives its candidates from
    current state, and one statement after the write the old amount is gone — this is the
    only point where both the old and the new figure exist.

    Not silent towards *operators* either. The fetch is best-effort by design, so a run
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
        notification_repository: NotificationRepository | None = None,
        settings_repository: NotificationSettingsRepository | None = None,
    ):
        self.renter_repository = renter_repository
        self.cpi_index_repository = cpi_index_repository
        # Both optional: without them the job still does its real work and simply stays
        # silent, which keeps it constructible in tests that only care about the math.
        self.notification_repository = notification_repository
        self.settings_repository = settings_repository
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
        def lookup(d: date) -> Optional[IndexReading]:
            row = self.cpi_index_repository.reading_on_or_before(self.index_id, d)
            return IndexReading(row.year, row.month, row.value) if row else None

        return lookup

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

    def _notify_rent_change(
        self,
        renter,
        old_years: list[dict],
        new_years: list[dict],
        base_index: Optional[float],
        today: date,
    ) -> int:
        """Raise the confirmation notification when *the year currently in effect* was
        repriced. Returns 1 if a row was written.

        Deliberately only the current year. Every monthly publication nudges the
        projections for years that haven't started, and reporting those would bury the
        one change that actually alters what the tenant owes this month.
        """
        if self.notification_repository is None or not renter.owner_id:
            return 0

        index = cpi.lease_year_index(renter.lease_start, len(new_years), today)
        if index is None:
            return 0
        old_amount = old_years[index].get("amount") if index < len(old_years) else None
        new_amount = new_years[index].get("amount")
        if old_amount is None or new_amount is None:
            return 0

        min_amount, min_percent = self._thresholds(renter.owner_id)
        if not cpi.is_material(old_amount, new_amount, min_amount, min_percent):
            return 0
        if self._notifications_muted(renter.owner_id):
            return 0

        anniversary = cpi.anniversary_of(renter.lease_start, index)
        reading = new_years[index].get("cpi_reading") or {}
        source = self.cpi_index_repository.reading_on_or_before(self.index_id, anniversary)
        if self.notification_repository.was_generated(
            renter.owner_id,
            NotificationTypeEnum.CPI_RENT_CHANGE,
            renter.id,
            anniversary.isoformat(),
            cpi.CHANGED_OFFSET,
        ):
            return 0

        self.notification_repository.create(
            owner_id=renter.owner_id,
            type=NotificationTypeEnum.CPI_RENT_CHANGE,
            entity_id=renter.id,
            period_key=anniversary.isoformat(),
            offset=cpi.CHANGED_OFFSET,
            data=cpi.build_data(
                cpi.STAGE_CHANGED,
                old_amount=old_amount,
                new_amount=new_amount,
                effective_date=anniversary,
                year_index=index,
                base_index=base_index,
                known_index=reading.get("value"),
                index_month=(
                    f"{reading['year']:04d}-{reading['month']:02d}"
                    if reading.get("year") and reading.get("month")
                    else None
                ),
                index_source=source.source if source else None,
            ),
        )
        return 1

    def _settings(self, owner_id: str):
        if self.settings_repository is None:
            return None
        return self.settings_repository.get(owner_id)

    def _thresholds(self, owner_id: str) -> tuple[float, float]:
        row = self._settings(owner_id)
        if row is None:
            return DEFAULT_CPI_MIN_CHANGE_AMOUNT, DEFAULT_CPI_MIN_CHANGE_PERCENT
        return row.cpi_min_change_amount, row.cpi_min_change_percent

    def _notifications_muted(self, owner_id: str) -> bool:
        """The master switch and per-event mute, applied here because this path never
        goes through the rules engine that enforces them for the other events."""
        row = self._settings(owner_id)
        if row is None:
            return False
        if not row.master_enabled:
            return True
        try:
            muted = json.loads(row.muted_events or "[]")
        except (json.JSONDecodeError, TypeError):
            return False
        return NotificationTypeEnum.CPI_RENT_CHANGE.value in muted

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

    def run_cpi_indexing(self, today: Optional[date] = None) -> dict:
        today = today or date.today()
        summary = {
            "fetched": 0,
            "renters_updated": 0,
            "renters_before_index_start": 0,
            "notified": 0,
            "source": None,
            "degraded": False,
            "stale": False,
        }

        # 1. Refresh the cache from the first source that answers: full backfill when
        #    empty, else the latest few months.
        superseded = self._refresh_cache(summary)
        # A period changing hands means anything frozen against the old value is now
        # anchored to a superseded reading, so re-derive the frozen bases this run — and
        # for the same reason lift the per-year freeze, since every amount downstream of
        # a provisional reading was derived from a number now known to be wrong.
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
                    current,
                    renter.lease_start,
                    base_rent,
                    lookup,
                    today=today,
                    force_recompute=reconcile_bases,
                )
            else:
                # Freeze the base index if it wasn't resolvable at signing (e.g. created
                # while the cache was empty); otherwise keep it fixed for the contract.
                # ``reconcile_bases`` is the one exception: a base frozen against a
                # provisional reading has to be re-anchored once the authoritative source
                # supersedes it, or it would stay on the fallback's value forever.
                base_index = renter.cpi_base_index
                if base_index is None or reconcile_bases:
                    base_index = _value_of(lookup(renter.lease_start))
                if base_index is None:
                    # No reading at or before this lease's start: it predates the cached
                    # history, so its rent silently holds at base_rent. Counted so a lease
                    # older than HISTORY_FLOOR shows up instead of quietly undercharging.
                    summary["renters_before_index_start"] += 1
                new_years = materialize_cpi_amounts(
                    current,
                    renter.lease_start,
                    base_rent,
                    base_index,
                    lookup,
                    today=today,
                    force_recompute=reconcile_bases,
                )

            if new_years != current or renter.cpi_base_index != base_index:
                # Raise the notification *before* the write, while `current` still holds
                # the amount the owner last knew about.
                summary["notified"] += self._notify_rent_change(
                    renter, current, new_years, base_index, today
                )
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

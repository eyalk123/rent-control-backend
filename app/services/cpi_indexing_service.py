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
  are filled in by the monthly indexing job once the reading exists.

The pure helpers below are the single source of truth for the math (used by both
renter create/update and the job) and are unit-testable with a fake ``index_lookup``.
"""
import json
import logging
from datetime import date
from typing import Callable, Optional

from dateutil.relativedelta import relativedelta

from app.config import settings
from app.repositories.cpi_index_repository import CpiIndexRepository
from app.repositories.renter_repository import RenterRepository
from app.services.cbs_index_service import CbsIndexService

logger = logging.getLogger(__name__)

IndexLookup = Callable[[date], Optional[float]]


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


class CpiIndexingService:
    """The monthly job: refresh the cached index from CBS, then recompute every
    CPI-linked renter's stored ``lease_years`` so newly-published readings take
    effect. Silent — no notifications (per product decision)."""

    def __init__(
        self,
        renter_repository: RenterRepository,
        cpi_index_repository: CpiIndexRepository,
        cbs_service: CbsIndexService,
        index_id: int | None = None,
    ):
        self.renter_repository = renter_repository
        self.cpi_index_repository = cpi_index_repository
        self.cbs_service = cbs_service
        self.index_id = index_id if index_id is not None else settings.CPI_INDEX_ID

    def _lookup(self) -> IndexLookup:
        return lambda d: self.cpi_index_repository.latest_on_or_before(self.index_id, d)

    def run_cpi_indexing(self) -> dict:
        summary = {"fetched": 0, "renters_updated": 0}

        # 1. Refresh the cache: full backfill when empty, else the latest few months.
        if self.cpi_index_repository.is_empty(self.index_id):
            rows = self.cbs_service.fetch_all()
        else:
            rows = self.cbs_service.fetch_latest()
        if rows:
            summary["fetched"] = self.cpi_index_repository.upsert_many(self.index_id, rows)

        # 2. Recompute every CPI-linked renter against the (now fresher) cache.
        lookup = self._lookup()
        for renter in self.renter_repository.get_by_escalation_mode("cpi"):
            if not renter.lease_start:
                continue
            try:
                current = json.loads(renter.lease_years) if renter.lease_years else []
            except (json.JSONDecodeError, TypeError):
                continue
            if not current:
                continue

            # Freeze the base index if it wasn't resolvable at signing (e.g. created
            # while the cache was empty); otherwise keep it fixed for the contract.
            base_index = renter.cpi_base_index
            if base_index is None:
                base_index = lookup(renter.lease_start)
            base_rent = renter.base_rent or current[0]["amount"]

            new_years = materialize_cpi_amounts(
                current, renter.lease_start, base_rent, base_index, lookup
            )
            if new_years != current or renter.cpi_base_index != base_index:
                self.renter_repository.update(
                    renter,
                    {"lease_years": json.dumps(new_years), "cpi_base_index": base_index},
                )
                summary["renters_updated"] += 1

        logger.info("CPI indexing: %s", summary)
        return summary

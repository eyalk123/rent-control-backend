"""Brings CPI readings published on different index bases onto one scale before caching.

CBS moves the CPI to a new base every two years ("2022 ממוצע" → "2024 ממוצע", January
2025) and restarts it near 100: December 2024 reads 108.4, January 2025 reads 101.5, though
prices rose 0.6%. Its API returns every month on the base it was published in, so two
readings either side of a change cannot be divided. Rent linkage divides two readings, and
a lease's base index is frozen as a bare number, so the cache has to hold **one scale for
good** — ``IndexSeries.scale_base``.

A reading on another base is converted with the chaining coefficient CBS publishes for the
change (מקדם קשר), read from the CBS calculator API — the same figure the CBS linkage
calculator uses, so a rent priced here matches it. Each base's coefficient is fetched once
and kept in ``cpi_base_factors``.

Best-effort like the index sources: when a coefficient cannot be fetched, readings on that
base are left out of this run rather than stored on the wrong scale. The cache then simply
lags, which the job's staleness check already reports, and the next run tries again.
"""
import logging
import re
from typing import Optional, Sequence

from app.repositories.cpi_base_factor_repository import CpiBaseFactorRepository
from app.services.index_source import fetch_json

logger = logging.getLogger(__name__)

# CBS spells a base "2024 ממוצע" (the 2024 average = 100); the Bank of Israel's SDMX unit
# for the same base is "I24_L" ("Index, 2024=100").
_CBS_AVERAGE_BASE = re.compile(r"^\s*(\d{4})\s*ממוצע\s*$")
_BOI_UNIT = re.compile(r"^I(\d{2})_L$")


def base_key(label: Optional[str]) -> Optional[str]:
    """One spelling for a base whichever publisher named it: "2024" for both "2024 ממוצע"
    and "I24_L". Anything else (CBS's pre-1980s bases are not year averages) is kept as
    given, which still compares correctly against itself."""
    if not label:
        return None
    match = _CBS_AVERAGE_BASE.match(label)
    if match:
        return match.group(1)
    match = _BOI_UNIT.match(label)
    if match:
        return f"20{match.group(1)}"
    return label.strip()


def _month_str(month: tuple[int, int]) -> str:
    return f"{month[0]:04d}-{month[1]:02d}"


def _calculator_date(month: tuple[int, int]) -> str:
    """The calculator takes a date and prices against the index known on it, two months
    earlier — so asking about month M means passing the first of M+2."""
    year, mon = month
    mon += 2
    if mon > 12:
        year, mon = year + 1, mon - 12
    return f"{mon:02d}-{year:04d}"


def _parse_month(text: object) -> Optional[tuple[int, int]]:
    try:
        year, month = str(text).split("-")
        return int(year), int(month)
    except ValueError:
        return None


class CpiScale:
    def __init__(self, repository: CpiBaseFactorRepository, cbs_base_url: str):
        self.repository = repository
        self.cbs_base_url = cbs_base_url.rstrip("/")

    def normalize(self, series, rows: Sequence[tuple]) -> tuple[list[tuple[int, int, float]], int]:
        """``rows`` as a source returns them — ``(year, month, value)`` with an optional
        fourth element naming the base — as ``(year, month, value)`` on the series' scale.
        Returns the rows and how many were left out for want of a coefficient."""
        if not series.scale_base:
            return [tuple(r[:3]) for r in rows], 0
        by_base: dict[Optional[str], list[tuple]] = {}
        for row in rows:
            base = base_key(row[3]) if len(row) > 3 else None
            by_base.setdefault(base, []).append(row)

        out: list[tuple[int, int, float]] = []
        dropped = 0
        for base, group in by_base.items():
            # No label means the source does not say; on-scale is the only reading that
            # keeps such a source working as it always has.
            if base is None or base == series.scale_base:
                out.extend((y, m, v) for y, m, v, *_ in group)
                continue
            # The newest month in the group: for the Bank of Israel, which rescales its
            # whole history to its current base, that is the one month certain to have
            # been *published* on that base too.
            sample = max((r[0], r[1]) for r in group)
            factor = self.factor(series, base, sample)
            if factor is None:
                dropped += len(group)
                continue
            out.extend((y, m, v * factor) for y, m, v, *_ in group)
        return out, dropped

    def factor(self, series, base: str, sample: tuple[int, int]) -> Optional[float]:
        stored = self.repository.get(series.series_id, base)
        if stored is not None:
            return stored.factor
        reference = series.scale_month
        older = sample < reference
        first, second = (sample, reference) if older else (reference, sample)
        answer = self._ask_calculator(series.series_id, first, second)
        if answer is None:
            return None
        got = (_parse_month(answer.get("from_index_date")), _parse_month(answer.get("to_index_date")))
        expected_base = base if older else series.scale_base
        coefficient = answer.get("chaining_coefficient")
        if (
            got != (first, second)
            or base_key(answer.get("base_year")) != expected_base
            or not isinstance(coefficient, (int, float))
            or coefficient <= 0
        ):
            logger.warning(
                "CPI scale: CBS calculator answered %s for %s→%s (base %s); not converting",
                answer,
                _month_str(first),
                _month_str(second),
                base,
            )
            return None
        # The coefficient brings the later month onto the earlier one's base:
        # ratio = later × c ÷ earlier. A base older than the scale is therefore divided by
        # it, a newer one multiplied.
        factor = 1 / coefficient if older else coefficient
        self.repository.add(
            index_id=series.series_id,
            base=base,
            factor=factor,
            coefficient=float(coefficient),
            from_month=_month_str(first),
            to_month=_month_str(second),
        )
        logger.info(
            "CPI scale: base %s → %s, CBS coefficient %s (%s→%s)",
            base,
            series.scale_base,
            coefficient,
            _month_str(first),
            _month_str(second),
        )
        return factor

    def _ask_calculator(
        self, index_id: int, first: tuple[int, int], second: tuple[int, int]
    ) -> Optional[dict]:
        data = fetch_json(
            f"{self.cbs_base_url}/index/data/calculator/{index_id}",
            {
                "value": 100,
                "date": _calculator_date(first),
                "toDate": _calculator_date(second),
                "format": "json",
                "download": "false",
            },
            logger,
            f"CBS calculator (id={index_id})",
        )
        answer = (data or {}).get("answer")
        return answer if isinstance(answer, dict) else None

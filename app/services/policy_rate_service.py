"""Keeps the Bank of Israel interest rate in `policy_rates`, for the mortgage rate-change alert.

Read from the same Bank of Israel SDMX service the CPI fallback uses (keyless, free).
Series ``MNT_RIB_BOI_D`` is "Bank Of Israel nominal interest rate", one observation per
calendar day; only the days it changes are stored.

Best-effort like the index sources: an unreachable upstream logs and stores nothing, and
the alert simply waits for the next run. A rate change is announced on the news anyway, so
a day's delay costs little, and a failure here must never stop the rent reminders that run
in the same job.
"""
import logging
from datetime import date, timedelta

from app.clock import utc_today
from app.repositories.policy_rate_repository import PolicyRateRepository
from app.services.index_source import fetch_json

logger = logging.getLogger(__name__)

COUNTRY = "IL"
_DATAFLOW = "BOI.STATISTICS/BR/1.0"
# The oldest change worth having: the backfill needs only enough history to know the
# rate before the next change. Alerts never look this far back (see ALERT_WINDOW_DAYS).
HISTORY_START = date(2023, 1, 1)
# A routine refresh re-reads this much, so a run missed for a few weeks still catches up.
REFRESH_LOOKBACK_DAYS = 60
# Israel's prime rate is the Bank of Israel rate plus this, by banking convention.
PRIME_SPREAD = 1.5


def prime_of(rate: float) -> float:
    return round(rate + PRIME_SPREAD, 2)


class PolicyRateService:
    def __init__(self, repository: PolicyRateRepository, base_url: str, series_code: str):
        self.repository = repository
        self.base_url = base_url.rstrip("/")
        self.series_code = series_code

    def refresh(self, today: date | None = None) -> dict:
        """Store any rate changes not stored yet. Returns how many were added."""
        # A system-wide job, so UTC — the same calendar the CPI refresh uses.
        today = today or utc_today()
        latest = self.repository.latest(COUNTRY)
        if latest is None:
            start, known_rate, known_date = HISTORY_START, None, None
        else:
            start = min(latest.effective_date, today - timedelta(days=REFRESH_LOOKBACK_DAYS))
            known_rate, known_date = latest.rate, latest.effective_date
        observations = self._fetch(start)
        if not observations:
            return {"added": 0, "fetched": False}
        changes = _changes(observations, known_rate, known_date)
        added = self.repository.add_all(COUNTRY, changes)
        if added:
            logger.info("Policy rate: %d change(s) stored, newest %s", added, changes[-1])
        return {"added": added, "fetched": True}

    def _fetch(self, start: date) -> list[tuple[date, float]]:
        url = f"{self.base_url}/data/dataflow/{_DATAFLOW}/{self.series_code}"
        data = fetch_json(
            url,
            {"format": "sdmx-json", "startPeriod": start.isoformat()},
            logger,
            f"BOI ({self.series_code})",
        )
        return _parse(data) if data else []


def _changes(
    observations: list[tuple[date, float]], known_rate: float | None, known_date: date | None
) -> list[tuple[date, float]]:
    """The days the rate moved, from daily observations, after what is already stored.

    With nothing stored the first observation is a change too: it is the rate the next
    change will be compared against."""
    out: list[tuple[date, float]] = []
    current = known_rate
    for day, rate in sorted(observations):
        if known_date is not None and day <= known_date:
            continue
        if current is None or rate != current:
            out.append((day, rate))
            current = rate
    return out


def _parse(data: dict) -> list[tuple[date, float]]:
    """``(day, rate)`` pairs from the SDMX-JSON response. Same shape the CPI fallback
    reads: observation keys are positions into the ``TIME_PERIOD`` values, values strings."""
    payload = data.get("data") or {}
    dims = ((payload.get("structure") or {}).get("dimensions") or {}).get("observation") or []
    if not dims:
        return []
    periods = [v.get("id") for v in dims[0].get("values") or []]
    rows: list[tuple[date, float]] = []
    for dataset in payload.get("dataSets") or []:
        for series in (dataset.get("series") or {}).values():
            for position, observation in (series.get("observations") or {}).items():
                try:
                    day = date.fromisoformat(periods[int(position)])
                    rate = float(observation[0])
                except (ValueError, IndexError, TypeError):
                    continue
                rows.append((day, rate))
    return rows

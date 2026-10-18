"""Fetches Consumer Price Index readings from the Bank of Israel's SDMX API.

**Fallback source.** BOI republishes the CBS series — series ``CP`` ("מדד המחירים לצרכן -
כללי" / "Consumer Price Index – Total") is the same series as CBS 120010. It exists here
because ``api.cbs.gov.il`` can be unreachable for days at a time, which would otherwise leave
every CPI-linked lease frozen at its base rent. CBS remains authoritative: readings written
by this source are provisional and CBS supersedes them (see
``CpiIndexRepository.upsert_many``).

Not verbatim, though. BOI chain-links the whole history onto CBS's *current* base (November
2023 is 105.1 at CBS on the 2022 base, 97.858… here on the 2024 one) and rescales it all
again at every base change. Each reading therefore carries the base the response names
(the ``UNIT_MEASURE`` dimension, e.g. ``I24_L`` = "Index, 2024=100"), and ``CpiScale``
converts it to the cache's scale like any CBS reading.

Free and keyless. The published series runs back to 1951; we request only from
``HISTORY_FLOOR`` via ``startPeriod``, and it arrives in a single response — unlike the CBS
API there is no pagination.

Response shape (SDMX-JSON)::

    {"data": {
        "dataSets": [{"series": {"<dim indices>": {"observations": {
            "897": ["104.8", ...], ...}}}}],
        "structure": {"dimensions": {"observation": [
            {"id": "TIME_PERIOD", "values": [..., {"id": "2026-06"}]}]}}}}

Observation keys are *positions* into that ``TIME_PERIOD`` list, and values arrive as
strings.
"""
import logging
import re
from datetime import date

from dateutil.relativedelta import relativedelta

from app.services.index_source import HISTORY_FLOOR, fetch_json

logger = logging.getLogger(__name__)

_DATAFLOW = "BOI.STATISTICS/PRI/1.0"
# The PRI data structure has 14 dimensions: SERIES_CODE, MAIN_SUBJECT, SUBJECT,
# PRICE_GROUP, FREQ, ADJUSTMENT, ACTIVITY_CBS_2011, ACTIVITY_CBS_1993, UNIT_MEASURE,
# CURRENCY_TRANS, TIME_COLLECT, TRANSFORMATION, CROC, HIERARCHY. We pin the first
# (the series code) and wildcard the rest, hence 13 trailing dots.
_WILDCARD_DIMENSIONS = "." * 13
_MONTHLY_PERIOD = re.compile(r"^(\d{4})-(\d{2})$")


class BoiIndexService:
    name = "boi"

    def __init__(
        self,
        base_url: str,
        series_code: str,
        history_floor: tuple[int, int] = HISTORY_FLOOR,
    ):
        self.base_url = base_url.rstrip("/")
        self.series_code = series_code
        self.history_floor = history_floor

    def fetch_all(self) -> list[tuple]:
        """History back to :data:`HISTORY_FLOOR` in one request — used to backfill an empty
        cache. ``startPeriod`` trims the response server-side, so this is a small payload."""
        return self._fetch(
            {"startPeriod": f"{self.history_floor[0]:04d}-{self.history_floor[1]:02d}"}
        )

    def fetch_latest(self, n: int = 6) -> list[tuple]:
        """The most recent ``n`` months — used for the routine refresh."""
        start = date.today() - relativedelta(months=n)
        return self._fetch({"startPeriod": f"{start.year:04d}-{start.month:02d}"})

    def _fetch(self, params: dict) -> list[tuple]:
        url = (
            f"{self.base_url}/data/dataflow/{_DATAFLOW}/"
            f"{self.series_code}{_WILDCARD_DIMENSIONS}"
        )
        data = fetch_json(
            url, {"format": "sdmx-json", **params}, logger, f"BOI ({self.series_code})"
        )
        return self._parse(data) if data else []

    @staticmethod
    def _parse(data: dict) -> list[tuple]:
        payload = data.get("data") or {}
        observation_dims = ((payload.get("structure") or {}).get("dimensions") or {}).get(
            "observation"
        ) or []
        if not observation_dims:
            return []
        periods = [v.get("id") for v in observation_dims[0].get("values") or []]
        series_dims = ((payload.get("structure") or {}).get("dimensions") or {}).get(
            "series"
        ) or []

        rows: list[tuple[int, int, float, str | None]] = []
        for dataset in payload.get("dataSets") or []:
            for key, series in (dataset.get("series") or {}).items():
                base = _unit_of(series_dims, key)
                for position, observation in (series.get("observations") or {}).items():
                    row = _reading(periods, position, observation)
                    if row is not None:
                        rows.append((*row, base))
        return rows


def _unit_of(series_dims: list, key: str) -> str | None:
    """The series' ``UNIT_MEASURE`` — which base its values are on (``I24_L``). The series
    key is one position per series dimension, e.g. ``0:0:0:0:0:0:0:0:0:0:0:0:0:0``."""
    positions = key.split(":")
    for i, dim in enumerate(series_dims):
        if dim.get("id") != "UNIT_MEASURE":
            continue
        try:
            return (dim.get("values") or [])[int(positions[i])].get("id")
        except (IndexError, ValueError, AttributeError):
            return None
    return None


def _reading(
    periods: list, position: str, observation: list
) -> tuple[int, int, float] | None:
    """One ``(year, month, value)`` from an SDMX observation, or ``None`` if it isn't a
    usable monthly reading. The wildcard key could in principle match a non-monthly
    series, so the period format is checked rather than assumed."""
    try:
        period = periods[int(position)]
    except (ValueError, IndexError):
        return None
    match = _MONTHLY_PERIOD.match(period or "")
    if not match:
        return None
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return None
    value = observation[0] if observation else None
    if value is None:
        return None
    try:
        return int(match.group(1)), month, float(value)
    except (TypeError, ValueError):
        return None

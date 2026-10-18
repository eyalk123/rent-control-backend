"""Fetches Consumer Price Index readings from the CBS (cbs.gov.il) public API.

The API is free and keyless. We hit the price-index endpoint and normalize the
nested response into ``(year, month, value)`` tuples for caching. Fetches are
best-effort: a failure is logged and surfaced as an empty list so the indexing job
never 500s on a government-API hiccup.

This is the **primary** source. CBS is the publisher Israeli lease escalation clauses
actually reference, so its readings are authoritative and supersede any fallback's;
see ``app/services/index_source.py`` and ``boi_index_service.py``.

Response shape (id=120010):
    {"month": [{"code", "name", "date": [
        {"year": 2026, "month": 5, "currBase": {"value": 104.8, "baseDesc": "2024 ממוצע"},
         ...}, ...
    ]}], ...}

``currBase`` is the base the month was *published* in, not today's: the history is not on
one scale. Readings carry it as a fourth element; see ``app/services/cpi_scale.py``.
"""
import logging

from app.services.index_source import HISTORY_FLOOR, at_or_after_floor, fetch_json

logger = logging.getLogger(__name__)

# The API caps each response at 100 readings and paginates the rest via ``next_url``.
_PAGE_SIZE = 100
_MAX_PAGES = 50  # safety bound (full general-CPI history is ~9 pages)


class CbsIndexService:
    name = "cbs"

    def __init__(
        self,
        base_url: str,
        index_id: int,
        history_floor: tuple[int, int] = HISTORY_FLOOR,
    ):
        self.base_url = base_url.rstrip("/")
        self.index_id = index_id
        self.history_floor = history_floor

    def fetch_all(self) -> list[tuple]:
        """History back to :data:`HISTORY_FLOOR` — used to backfill an empty cache. Follows
        the API's ``next_url`` pagination (100 readings/page).

        The API has no start-date filter, so the floor is applied client-side. Readings come
        back newest-first, which lets us stop as soon as a page holds nothing at or after the
        floor — one request instead of nine. If that ordering ever changes we simply page to
        the end as before and filter, so this is an optimization, not a correctness
        assumption."""
        rows: list[tuple] = []
        url: str | None = f"{self.base_url}/index/data/price"
        params: dict | None = {
            "id": self.index_id,
            "format": "json",
            "download": "false",
            "PageSize": _PAGE_SIZE,
        }
        for _ in range(_MAX_PAGES):
            if not url:
                break
            data = self._get(url, params)
            if data is None:
                break
            page = self._parse(data)
            kept = at_or_after_floor(page, self.history_floor)
            rows.extend(kept)
            if page and not kept:
                break  # walked past the floor; everything beyond is older still
            url = (data.get("paging") or {}).get("next_url")
            params = None  # next_url already carries the query string
        return rows

    def fetch_latest(self, n: int = 6) -> list[tuple]:
        """The most recent ``n`` readings — used for the monthly refresh."""
        data = self._get(
            f"{self.base_url}/index/data/price",
            {"id": self.index_id, "format": "json", "download": "false", "last": n},
        )
        return self._parse(data) if data else []

    def _get(self, url: str, params: dict | None) -> dict | None:
        return fetch_json(url, params, logger, f"CBS (id={self.index_id})")

    @staticmethod
    def _parse(data: dict) -> list[tuple]:
        rows: list[tuple] = []
        for series in data.get("month") or []:
            for reading in series.get("date") or []:
                year = reading.get("year")
                month = reading.get("month")
                current = reading.get("currBase") or {}
                value = current.get("value")
                if year is None or month is None or value is None:
                    continue
                # Each month comes on the base it was published in ("2022 ממוצע" until
                # December 2024, "2024 ממוצע" from January 2025); CpiScale evens them out.
                rows.append((int(year), int(month), float(value), current.get("baseDesc")))
        return rows

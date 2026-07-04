"""Fetches Consumer Price Index readings from the CBS (cbs.gov.il) public API.

The API is free and keyless. We hit the price-index endpoint and normalize the
nested response into ``(year, month, value)`` tuples for caching. Fetches are
best-effort: a failure is logged and surfaced as an empty list so the monthly
indexing job never 500s on a government-API hiccup.

Response shape (id=120010):
    {"month": [{"code", "name", "date": [
        {"year": 2026, "month": 5, "currBase": {"value": 104.8, ...}, ...}, ...
    ]}], ...}
"""
import logging

import requests

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20
# The API caps each response at 100 readings and paginates the rest via ``next_url``.
_PAGE_SIZE = 100
_MAX_PAGES = 50  # safety bound (full general-CPI history is ~9 pages)


class CbsIndexService:
    def __init__(self, base_url: str, index_id: int):
        self.base_url = base_url.rstrip("/")
        self.index_id = index_id

    def fetch_all(self) -> list[tuple[int, int, float]]:
        """Full historical series — used to backfill an empty cache. Follows the
        API's ``next_url`` pagination (100 readings/page) to the end."""
        rows: list[tuple[int, int, float]] = []
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
            rows.extend(self._parse(data))
            url = (data.get("paging") or {}).get("next_url")
            params = None  # next_url already carries the query string
        return rows

    def fetch_latest(self, n: int = 6) -> list[tuple[int, int, float]]:
        """The most recent ``n`` readings — used for the monthly refresh."""
        data = self._get(
            f"{self.base_url}/index/data/price",
            {"id": self.index_id, "format": "json", "download": "false", "last": n},
        )
        return self._parse(data) if data else []

    def _get(self, url: str, params: dict | None) -> dict | None:
        try:
            response = requests.get(url, params=params, timeout=_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("CBS index fetch failed (id=%s): %s", self.index_id, exc)
            return None

    @staticmethod
    def _parse(data: dict) -> list[tuple[int, int, float]]:
        rows: list[tuple[int, int, float]] = []
        for series in data.get("month") or []:
            for reading in series.get("date") or []:
                year = reading.get("year")
                month = reading.get("month")
                value = (reading.get("currBase") or {}).get("value")
                if year is None or month is None or value is None:
                    continue
                rows.append((int(year), int(month), float(value)))
        return rows

"""Keeps the tax tracks report's government figures in `tax_values`, per country and tax year.

Each country with the report on names a source (``CountryConfig.tax_data``); Israel's is the
Tax Authority's yearly booklet plus the rates in the law (``il_tax_source``). On each run:

- **Law values** are written for every year from ``first_year`` to this one. They come from
  code and change only when the law does, so this is normally a no-op.
- **A year with no booklet stored** is tried every run — that is how a new year's booklet,
  published some time in January, arrives within a day. Not published yet is not an error.
- **This year's booklet** is downloaded again once a week, and in January last year's too,
  for a late republication. An unchanged file (same fingerprint) changes nothing.
- **A changed file** is read, and its figures replace the year's, with the new fingerprint.

Best-effort like the policy rate refresh: an unreachable upstream logs and stores nothing,
and the next run tries again. A file that downloads but does not read is different — the
booklet's layout changed, and someone has to look — so it is reported to Sentry, and the
year keeps the figures it had.
"""
import hashlib
import logging
from datetime import date, datetime, timedelta

import requests
import sentry_sdk

from app.clock import utc_now_naive, utc_today
from app.repositories.tax_value_repository import TaxValueRepository
from app.services import country_service, il_tax_source

logger = logging.getLogger(__name__)

RECHECK_EVERY = timedelta(days=7)
FETCH_TIMEOUT_SECONDS = 30
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

#: Every tax source the app can read, by ``TaxData.source``.
SOURCES = {il_tax_source.name: il_tax_source}


class TaxDataService:
    def __init__(self, repository: TaxValueRepository, sources: dict | None = None):
        self.repository = repository
        self.sources = SOURCES if sources is None else sources

    def refresh(self, today: date | None = None) -> dict:
        # A system-wide job, so UTC — the same calendar the CPI refresh uses.
        today = today or utc_today()
        now = utc_now_naive()
        summary = {"law_values_changed": 0, "checked": 0, "updated": [], "failed": []}
        for country, tax_data in country_service.tax_data_by_country().items():
            source = self.sources.get(tax_data.source)
            if source is None:
                logger.error("Tax data: no reader named %s for %s", tax_data.source, country)
                continue
            for year in range(tax_data.first_year, today.year + 1):
                summary["law_values_changed"] += self.repository.sync_law_values(
                    country, year, source.law_values(year)
                )
                if self._due(country, year, today, now):
                    summary["checked"] += 1
                    self._check(country, year, source, now, summary, past=year < today.year)
        return summary

    def _due(self, country: str, year: int, today: date, now: datetime) -> bool:
        document = self.repository.document(country, year)
        if document is None:
            return True
        recent = year == today.year or (year == today.year - 1 and today.month == 1)
        return recent and now - document.checked_at >= RECHECK_EVERY

    def _check(
        self, country: str, year: int, source, now: datetime, summary: dict, past: bool
    ) -> None:
        url = source.booklet_url(year)
        stored = self.repository.document(country, year)
        status, content = fetch_document(url)
        if status == "missing":
            if past:
                # A past year's booklet should exist. Logged, not raised: the report says
                # the year's figures are missing, which is the honest answer meanwhile.
                logger.warning("Tax data: %s booklet for %s not found at %s", country, year, url)
            return
        if content is None:
            return
        sha256 = hashlib.sha256(content).hexdigest()
        if stored is not None and stored.sha256 == sha256:
            self.repository.mark_checked(stored, now)
            return
        try:
            values = source.read_booklet(content)
        except ValueError as exc:  # the source's "did not read" (BookletError)
            summary["failed"].append(f"{country}-{year}")
            logger.error("Tax data: %s %s booklet did not read: %s", country, year, exc)
            # Logging alone does not reach Sentry (LoggingIntegration's event_level is None).
            with sentry_sdk.new_scope() as scope:
                scope.set_context("tax_booklet", {"url": url, "sha256": sha256, "error": str(exc)})
                sentry_sdk.capture_message(
                    f"Tax booklet did not read: {country} {year}", level="error"
                )
            return
        self.repository.save_document(country, year, url, sha256, values, now)
        summary["updated"].append(f"{country}-{year}")
        logger.info(
            "Tax data: %s %s booklet %s",
            country,
            year,
            "read" if stored is None else "republished and read again",
        )


def fetch_document(url: str) -> tuple[str, bytes | None]:
    """``("ok", bytes)``, ``("missing", None)`` on a 404, or ``("error", None)``."""
    try:
        with requests.get(url, timeout=FETCH_TIMEOUT_SECONDS, stream=True) as response:
            if response.status_code == 404:
                return "missing", None
            response.raise_for_status()
            content = b""
            for chunk in response.iter_content(chunk_size=65536):
                content += chunk
                if len(content) > MAX_DOCUMENT_BYTES:
                    logger.warning("Tax data: %s is larger than expected, skipped", url)
                    return "error", None
    except requests.RequestException as exc:
        logger.warning("Tax data: fetching %s failed: %s", url, exc)
        return "error", None
    if not content.startswith(b"%PDF"):
        logger.warning("Tax data: %s did not return a PDF", url)
        return "error", None
    return "ok", content

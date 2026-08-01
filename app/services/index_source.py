"""Common contract for the CPI index feeds.

An index source normalizes whatever its upstream returns into ``(year, month, value)``
tuples. Sources are tried in order by :class:`~app.services.cpi_indexing_service.CpiIndexingService`
and the first one returning rows wins, so they must be interchangeable: same tuple shape,
same best-effort failure mode (log and return an empty list, never raise). A source that
raised would take down the whole job on one upstream hiccup.

Precedence between sources is *not* decided here — it lives in
:meth:`~app.repositories.cpi_index_repository.CpiIndexRepository.upsert_many`, keyed on
``name``, so a fallback can fill gaps without overwriting the authoritative feed.
"""
import logging
from typing import Protocol

import requests

# Upstreams that are up answer in well under a second. A short ceiling keeps a *dead*
# upstream from stalling every run — the whole point of having a second source is that
# falling through to it is cheap.
FETCH_TIMEOUT_SECONDS = 8

# Oldest month worth caching. The full published series runs back to 1951, which is ~900
# readings we have no use for: the index is only ever read at a lease's start date and its
# anniversaries, so history only needs to reach the oldest lease anyone will enter.
#
# The floor is 2019-11, not 2020-01, deliberately. A lease starting 2020-01-01 resolves its
# base from the *known* index (המדד הידוע) — two months earlier, 2019-11. Setting the floor
# at the intended earliest lease year would leave leases signed that January unresolvable.
#
# A lease older than this floor cannot freeze a base index and silently holds at its
# original rent, so ``CpiIndexingService`` counts those and warns rather than letting it
# pass unnoticed. If real users start entering older leases, lower this and let the next
# run re-backfill.
HISTORY_FLOOR = (2019, 11)


def at_or_after_floor(rows: list[tuple[int, int, float]]) -> list[tuple[int, int, float]]:
    """Drop readings older than :data:`HISTORY_FLOOR`."""
    cutoff = HISTORY_FLOOR[0] * 12 + HISTORY_FLOOR[1]
    return [r for r in rows if r[0] * 12 + r[1] >= cutoff]


class IndexSource(Protocol):
    """One CPI feed. ``name`` is persisted as ``cpi_index.source``."""

    name: str

    def fetch_all(self) -> list[tuple[int, int, float]]:
        """Full historical series — used to backfill an empty cache."""
        ...

    def fetch_latest(self, n: int = 6) -> list[tuple[int, int, float]]:
        """The most recent ``n`` months — used for the routine refresh."""
        ...


def fetch_json(
    url: str, params: dict | None, logger: logging.Logger, label: str
) -> dict | None:
    """GET ``url`` and decode JSON, or ``None`` if the upstream failed in any way.

    Best-effort by design: a network error, an HTTP error, or a non-JSON body all log a
    warning and yield ``None`` so the caller can fall through to the next source. The
    caller — not this helper — is responsible for making a *persistent* failure visible;
    see the staleness check in ``CpiIndexingService``.
    """
    try:
        response = requests.get(url, params=params, timeout=FETCH_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("%s index fetch failed: %s", label, exc)
        return None

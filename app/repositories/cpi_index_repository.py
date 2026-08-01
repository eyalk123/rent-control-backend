from datetime import date

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cpi_index import CpiIndex


# Which source's reading wins when two disagree about the same month. CBS is the
# publisher lease contracts reference, so it outranks any republisher. An unknown
# source ranks 0 — it can fill an empty month but never displace a known one.
_SOURCE_RANK = {"cbs": 2, "boi": 1}


def reference_period(d: date) -> tuple[int, int]:
    """The ``(year, month)`` of the latest CPI month whose reading is *published* on
    or before ``d`` — the "known index" (המדד הידוע).

    A month's index publishes ~the 15th of the following month, so on any date the
    newest known reading is one month back if we're past the 15th, otherwise two
    months back.
    """
    ref = date(d.year, d.month, 1) - relativedelta(months=1 if d.day >= 15 else 2)
    return ref.year, ref.month


def _reference_period_key(d: date) -> int:
    """``year*12 + month`` key of :func:`reference_period` (for SQL comparison)."""
    year, month = reference_period(d)
    return year * 12 + month


class CpiIndexRepository:
    def __init__(self, session: Session):
        self.session = session

    def is_empty(self, index_id: int) -> bool:
        stmt = select(CpiIndex.id).where(CpiIndex.index_id == index_id).limit(1)
        return self.session.scalar(stmt) is None

    def reading_on_or_before(self, index_id: int, d: date) -> CpiIndex | None:
        """The most recent index *reading* known (published) on or before ``d`` — the
        row a contract's anniversary should use, or ``None`` if none is cached yet.
        Exposes the reading's (year, month) so callers can tell whether the exact
        known-index month is available (finalized) or an older one is standing in
        (projected)."""
        ref_key = _reference_period_key(d)
        period_key = CpiIndex.year * 12 + CpiIndex.month
        stmt = (
            select(CpiIndex)
            .where(CpiIndex.index_id == index_id, period_key <= ref_key)
            .order_by(period_key.desc())
            .limit(1)
        )
        return self.session.scalar(stmt)

    def latest_on_or_before(self, index_id: int, d: date) -> float | None:
        """The most recent index value known (published) on or before date ``d`` —
        the reading a contract's anniversary should use. ``None`` when no such
        reading is cached yet (e.g. the month hasn't been published/fetched)."""
        row = self.reading_on_or_before(index_id, d)
        return row.value if row else None

    def latest_period(self, index_id: int) -> tuple[int, int] | None:
        """The newest ``(year, month)`` cached for the series, or ``None`` when empty.
        Compare against :func:`reference_period` to tell how stale the cache is."""
        period_key = CpiIndex.year * 12 + CpiIndex.month
        stmt = (
            select(CpiIndex.year, CpiIndex.month)
            .where(CpiIndex.index_id == index_id)
            .order_by(period_key.desc())
            .limit(1)
        )
        row = self.session.execute(stmt).first()
        return (row.year, row.month) if row else None

    def earliest_period(self, index_id: int) -> tuple[int, int] | None:
        """The oldest ``(year, month)`` cached — how far back the history reaches. A lease
        starting before it cannot resolve a base index."""
        period_key = CpiIndex.year * 12 + CpiIndex.month
        stmt = (
            select(CpiIndex.year, CpiIndex.month)
            .where(CpiIndex.index_id == index_id)
            .order_by(period_key.asc())
            .limit(1)
        )
        row = self.session.execute(stmt).first()
        return (row.year, row.month) if row else None

    def upsert_many(
        self, index_id: int, rows: list[tuple[int, int, float]], source: str
    ) -> tuple[int, set[tuple[int, int]]]:
        """Merge ``(year, month, value)`` readings from ``source`` into the cache.

        Sources are ranked (see :data:`_SOURCE_RANK`) and a *lower*-ranked source never
        overwrites a higher-ranked reading — a fallback fills gaps CBS hasn't covered,
        but if the two ever disagree at the same month (a revision, a base change handled
        differently) the authoritative value stands. A higher-ranked source does overwrite,
        which is how CBS reclaims months served by the fallback while it was down.

        Portable across SQLite (tests) and Postgres. Returns ``(changed, superseded)``
        where ``changed`` counts rows inserted or updated and ``superseded`` is the set of
        periods whose source was upgraded — the caller re-derives anything frozen against
        those older values.
        """
        rank = _SOURCE_RANK.get(source, 0)
        existing = {
            (r.year, r.month): r
            for r in self.session.scalars(
                select(CpiIndex).where(CpiIndex.index_id == index_id)
            )
        }
        changed = 0
        superseded: set[tuple[int, int]] = set()
        for year, month, value in rows:
            row = existing.get((year, month))
            if row is None:
                self.session.add(
                    CpiIndex(
                        index_id=index_id,
                        year=year,
                        month=month,
                        value=value,
                        source=source,
                    )
                )
                changed += 1
                continue
            existing_rank = _SOURCE_RANK.get(row.source, 0)
            if rank < existing_rank:
                continue  # provisional reading; the authoritative one already stands
            if rank > existing_rank:
                superseded.add((year, month))
                row.source = source
                if row.value != value:
                    row.value = value
                changed += 1
            elif row.value != value:
                row.value = value
                changed += 1
        self.session.commit()
        return changed, superseded

from datetime import date

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cpi_index import CpiIndex


def _reference_period_key(d: date) -> int:
    """The comparable ``year*12 + month`` key of the latest CPI month whose reading
    is *published* on or before ``d``.

    A month's index publishes ~the 15th of the following month, so on any date the
    newest "known index" (המדד הידוע) is the reading from one month back if we're
    past the 15th, otherwise two months back.
    """
    ref = date(d.year, d.month, 1) - relativedelta(months=1 if d.day >= 15 else 2)
    return ref.year * 12 + ref.month


class CpiIndexRepository:
    def __init__(self, session: Session):
        self.session = session

    def is_empty(self, index_id: int) -> bool:
        stmt = select(CpiIndex.id).where(CpiIndex.index_id == index_id).limit(1)
        return self.session.scalar(stmt) is None

    def latest_on_or_before(self, index_id: int, d: date) -> float | None:
        """The most recent index value known (published) on or before date ``d`` —
        the reading a contract's anniversary should use. ``None`` when no such
        reading is cached yet (e.g. the month hasn't been published/fetched)."""
        ref_key = _reference_period_key(d)
        period_key = CpiIndex.year * 12 + CpiIndex.month
        stmt = (
            select(CpiIndex.value)
            .where(CpiIndex.index_id == index_id, period_key <= ref_key)
            .order_by(period_key.desc())
            .limit(1)
        )
        return self.session.scalar(stmt)

    def upsert_many(self, index_id: int, rows: list[tuple[int, int, float]]) -> int:
        """Insert new (year, month, value) readings and update any whose value
        changed. Portable across SQLite (tests) and Postgres. Returns the number of
        rows inserted or changed."""
        existing = {
            (r.year, r.month): r
            for r in self.session.scalars(
                select(CpiIndex).where(CpiIndex.index_id == index_id)
            )
        }
        changed = 0
        for year, month, value in rows:
            row = existing.get((year, month))
            if row is None:
                self.session.add(
                    CpiIndex(index_id=index_id, year=year, month=month, value=value)
                )
                changed += 1
            elif row.value != value:
                row.value = value
                changed += 1
        self.session.commit()
        return changed

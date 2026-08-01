from sqlalchemy import Column, Float, Integer, String, UniqueConstraint

from app.models.base import Base


class CpiIndex(Base):
    """A cached Consumer Price Index reading.

    One row per (series, month). ``index_id`` is the CBS series id (e.g. 120010 =
    the general CPI) so the same table can hold more series later. Values are the
    published index level for that month; the CBS revises only very recent housing
    readings, not the general CPI, so cached rows are effectively final.

    ``source`` records which feed the reading came from — ``cbs`` (the contractual
    publisher) or ``boi`` (the Bank of Israel's republication of the same series,
    used when CBS is unreachable). It exists to enforce precedence: a ``boi`` row
    is provisional and CBS supersedes it, never the other way round. That makes the
    provenance of every cached reading auditable if a rent figure is ever disputed.
    """

    __tablename__ = "cpi_index"

    id = Column(Integer, primary_key=True, autoincrement=True)
    index_id = Column(Integer, nullable=False)  # CBS series id, e.g. 120010
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)  # 1-12
    value = Column(Float, nullable=False)  # published index level
    source = Column(String, nullable=False, server_default="cbs")  # "cbs" | "boi"

    __table_args__ = (
        UniqueConstraint("index_id", "year", "month", name="uq_cpi_index_series_period"),
    )

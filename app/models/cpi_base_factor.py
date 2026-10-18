from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint

from app.clock import utc_now_naive
from app.models.base import Base


class CpiBaseFactor(Base):
    """How to bring a reading published on one index base onto the series' stored scale.

    CBS moves the CPI to a new base every two years and restarts it near 100, so December
    2024 reads 108.4 on the "2022 average" base and January 2025 reads 101.5 on the "2024
    average" one. With each change CBS publishes a chaining coefficient (מקדם קשר) between
    the two; ``factor`` is that coefficient applied in the right direction, so
    ``reading × factor`` is the reading on ``IndexSeries.scale_base``.

    Fetched once per base from the CBS calculator API and kept: a published coefficient
    never changes. ``coefficient`` and the two months it was asked for are kept beside the
    factor so a figure can be checked against the CBS calculator by hand.
    """

    __tablename__ = "cpi_base_factors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    index_id = Column(Integer, nullable=False)
    base = Column(String, nullable=False)  # as cpi_scale.base_key spells it, e.g. "2022"
    factor = Column(Float, nullable=False)
    coefficient = Column(Float, nullable=False)  # as CBS published it
    from_month = Column(String, nullable=False)  # "2024-12": the earlier of the two months
    to_month = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

    __table_args__ = (
        UniqueConstraint("index_id", "base", name="uq_cpi_base_factors_index_base"),
    )

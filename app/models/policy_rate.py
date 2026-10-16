from sqlalchemy import Column, Date, DateTime, Float, Integer, String, UniqueConstraint

from app.clock import utc_now_naive
from app.models.base import Base


class PolicyRate(Base):
    """A central-bank interest rate, from the day it took effect.

    One row per *change*, not per day: the Bank of Israel publishes its rate daily, but it
    moves only on decision days, a few times a year, and a change is the only thing anything
    here reads. What a mortgage's prime track follows — in Israel prime is this rate + 1.5.

    Not the `cpi_index` table, on purpose: that is a monthly price index read at a lease's
    anniversaries, this is a percentage that changes on arbitrary dates. Different shape,
    different reader. `country` keeps a second market's rate from needing a new table.
    """

    __tablename__ = "policy_rates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    country = Column(String(2), nullable=False)
    effective_date = Column(Date, nullable=False)
    rate = Column(Float, nullable=False)  # percent, e.g. 3.25
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

    __table_args__ = (
        UniqueConstraint("country", "effective_date", name="uq_policy_rates_country_date"),
    )

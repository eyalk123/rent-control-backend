from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint

from app.clock import utc_now_naive
from app.models.base import Base


class TaxYearInput(Base):
    """What the account holder told the tax report about one group's tax year.

    The marginal track needs the household's income outside renting (to find the bracket its
    rent lands in, and the surtax) and, when that income is low, how many credit points it
    has. Neither belongs to a property or an owner, so it is kept here per group and year.

    ``group_key`` is the report's key for the group: ``owner:<id>``, ``custom:<id>`` or
    ``none``. The default groups have no row of their own, so the key is what ties the
    answer to them. A key whose group no longer exists is left alone (the answer comes back
    if the group does); a deleted custom group's rows go with it.

    Affects only the tax report. Never sent to the assistant.
    """

    __tablename__ = "tax_year_inputs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    group_key = Column(String(40), nullable=False)
    year = Column(Integer, nullable=False)
    # Annual, as typed. None until the account holder answers.
    other_income = Column(Float, nullable=True)
    # Only asked when other income is too low to use up the basic points.
    credit_points = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)

    __table_args__ = (
        UniqueConstraint("owner_id", "group_key", "year", name="uq_tax_year_inputs_group_year"),
    )

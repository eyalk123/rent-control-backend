from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint

from app.clock import utc_now_naive
from app.models.base import Base


class TaxYearInput(Base):
    """What the account holder told the tax report about one person's tax year.

    The marginal track needs the person's income outside renting (to find the bracket their
    rent lands in, and the surtax) and, when that income is low, how many credit points they
    have. Neither is kept on the owner, because both change every year.

    ``person_key`` is ``owner:<id>`` for a property owner, or ``none`` for the properties
    that have no owner. A key whose owner no longer exists is left alone.

    Affects only the tax report. Never sent to the assistant.
    """

    __tablename__ = "tax_year_inputs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    person_key = Column(String(40), nullable=False)
    year = Column(Integer, nullable=False)
    # Annual, as typed. None until the account holder answers.
    other_income = Column(Float, nullable=True)
    # Only asked when other income is too low to use up the basic points.
    credit_points = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)

    __table_args__ = (
        UniqueConstraint("owner_id", "person_key", "year", name="uq_tax_year_inputs_person_year"),
    )

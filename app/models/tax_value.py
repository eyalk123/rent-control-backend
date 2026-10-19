from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from app.clock import utc_now_naive
from app.models.base import Base


class TaxDocument(Base):
    """A government publication the tax figures are read from, one per country and tax year.

    In Israel, the Tax Authority's yearly withholding booklet. ``sha256`` is the fingerprint
    of the file last read: a republished booklet (2026's was, in April) has a new one, which
    is what makes the refresh re-read it. ``checked_at`` is the last time anyone looked,
    whether or not the file had changed — it paces the weekly re-check.

    No tenant data: a public file's address and fingerprint.
    """

    __tablename__ = "tax_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    country = Column(String(2), nullable=False)
    tax_year = Column(Integer, nullable=False)
    url = Column(String, nullable=False)
    sha256 = Column(String(64), nullable=False)
    # When this version of the file was first read.
    fetched_at = Column(DateTime, nullable=False, default=utc_now_naive)
    checked_at = Column(DateTime, nullable=False, default=utc_now_naive)

    __table_args__ = (
        UniqueConstraint("country", "tax_year", name="uq_tax_documents_country_year"),
    )


class TaxValue(Base):
    """One government figure for one country and tax year, with where it came from.

    Two kinds, told apart by ``source``:

    - ``document`` — read from the year's publication (``TaxDocument``): the exemption
      ceiling, the brackets, the surtax threshold, the credit point. ``url`` and ``sha256``
      name the exact file.
    - ``law`` — a rate written in the law itself (10%, the 3% surtax, 2% depreciation…),
      which no publication restates in a form worth parsing. ``reference`` names the
      section, ``url`` where it was checked. These change only when the law does.

    ``value`` is JSON: a number for most keys, a list of brackets for the bracket keys.
    """

    __tablename__ = "tax_values"

    id = Column(Integer, primary_key=True, autoincrement=True)
    country = Column(String(2), nullable=False)
    tax_year = Column(Integer, nullable=False)
    key = Column(String(64), nullable=False)
    value = Column(Text, nullable=False)
    source = Column(String(16), nullable=False)  # document | law
    url = Column(String, nullable=False)
    reference = Column(Text, nullable=True)
    sha256 = Column(String(64), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive)

    __table_args__ = (
        UniqueConstraint("country", "tax_year", "key", name="uq_tax_values_country_year_key"),
    )

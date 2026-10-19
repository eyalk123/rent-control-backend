from sqlalchemy import Column, DateTime, Integer, String, Text

from app.clock import utc_now_naive
from app.models.base import Base


class TaxGroup(Base):
    """A household the tax tracks report treats as one, built by the account holder.

    By default every property owner is a group of their own and the properties with no owner
    form one more. A group here overrides that: owners of one household (a spouse living with
    them, children under 18) share one exemption ceiling, so their rent is tested together.
    A group can also be a hand-picked set of properties.

    ``property_owner_ids`` and ``property_ids`` are JSON lists. An owner or a property belongs
    to at most one group, which the service checks on save. Ids that no longer exist (an owner
    or property deleted since) are skipped when the report is built, not cleaned up here.

    Affects only the tax report.
    """

    __tablename__ = "tax_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    property_owner_ids = Column(Text, nullable=False, default="[]")
    property_ids = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)

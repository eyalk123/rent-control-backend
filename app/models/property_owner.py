from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint

from app.clock import utc_now_naive
from app.models.base import Base


class PropertyOwner(Base):
    """The *human* who owns a property — a parent, a spouse, a partnership — as distinct
    from ``owner_id``, the account holder who manages it. Not to be confused with the
    ``owners`` table, which is the account itself.

    The name is unique within an account and kept exactly as typed. Reports, notification
    scopes and export history still refer to an owner by name, so the uniqueness is what
    keeps a name a reliable key; renaming goes through ``PropertyOwnerService`` so the
    references move with it.
    """

    __tablename__ = "property_owners"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_property_owners_owner_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    bank_account = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)

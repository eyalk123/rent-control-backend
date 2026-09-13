from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint

from app.clock import utc_now_naive
from app.models.base import Base


class CountryNotifyRequest(Base):
    """"Tell me when you add {Country}" — an owner asking for their market to be built.

    Optional and non-blocking. No country is ever refused an account, so this is a
    preference, not a waiting list: the user is already inside the product when they
    ask. That is also why it is owner-scoped rather than an anonymous email capture —
    there is always an account to hang it off, and it is swept by account deletion like
    everything else.

    It is the cheapest demand signal available for deciding which country earns a native
    pack next, and unlike signup counts it separates "landed here" from "wants this".
    """

    __tablename__ = "country_notify_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    country_code = Column(String(2), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

    # One request per owner per country. Asking twice is the same fact, not a stronger
    # one, and counting it twice would skew the only signal this table exists to give.
    __table_args__ = (
        UniqueConstraint("owner_id", "country_code", name="uq_country_notify_owner_country"),
    )

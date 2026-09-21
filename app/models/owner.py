from sqlalchemy import Column, DateTime, String, Text

from app.clock import utc_now_naive
from app.models.base import Base


class Owner(Base):
    """Profile mirror of an authenticated owner, synced from the Firebase ID token.

    Firebase Auth remains the source of truth for authentication; this table exists so
    owner contact/profile info is queryable in SQL (emailing, admin tooling, analytics).
    ``id`` is the Firebase UID — the same value stored as ``owner_id`` on every other table.
    """

    __tablename__ = "owners"

    id = Column(String, primary_key=True)  # Firebase UID (== owner_id elsewhere)
    email = Column(String, nullable=True, index=True)
    display_name = Column(String, nullable=True)
    picture_url = Column(String, nullable=True)
    # Onboarding progress as a JSON document — see revision 045 for the shape and why it
    # lives on the account rather than in device storage. Two maps, deliberately apart:
    # `seeds_shown` records that a feature was *named* somewhere, `tours_seen` that it was
    # *explained*. Seeing the seed must never consume the destination tour.
    tour_state = Column(Text, nullable=False, server_default="{}", default="{}")
    # ISO 3166-1 alpha-2, chosen once at signup. Drives currency, formats and the default
    # for new properties. NULL means "not chosen yet", which is what the signup country
    # gate keys off — so it must stay nullable. Rules read `Property.country`, not this.
    country = Column(String(2), nullable=True)
    # ISO 4217, chosen beside the country. NULL means "not chosen — use the country's
    # own", which is every account that predates the picker. Read through
    # `country_service.effective_currency`, never directly: a stored code that is not in
    # the currency table falls back to the country rather than inventing a symbol.
    #
    # Locked once the account has a property. `properties.currency_code` is frozen at
    # creation, so changing this later would leave recorded amounts stored in one currency
    # and relabelled in another — a ₪5,000 rent silently reading as $5,000.
    currency = Column(String(3), nullable=True)
    # BCP-47, and the reason it is here rather than on the device: language used to live in
    # `localStorage` / `AsyncStorage`, so it did not survive signing in on a second device.
    # NULL means "not chosen", which falls back to the device's own language.
    language = Column(String(5), nullable=True)
    # A plan granted outright, independent of anything ever being paid. NULL is the
    # normal case; every account that existed before billing was introduced carries the
    # top tier here, permanently, because they built their portfolio on a product that
    # made no such demand and it would be a breach of that to start now.
    #
    # Separate from the `subscriptions` row on purpose. Writing the grant there would put
    # it in the path of webhook upserts, and a grandfathered landlord who subscribed and
    # later cancelled would silently lose a grant that was never conditional on payment.
    # Entitlement takes whichever of the two permits more — see
    # `entitlement_service.better_plan`.
    granted_plan = Column(String, nullable=True)
    # The plan the landlord last acknowledged the "some properties are locked" notice
    # for. NULL means never acknowledged.
    #
    # It stores the *plan*, not a boolean, so the notice reappears when the situation
    # genuinely changes. Someone who acknowledges on the free plan, upgrades, then later
    # downgrades to a different band is in a new situation with a different number of
    # locked properties, and a "seen it once" flag would leave them to work that out
    # unaided.
    lock_notice_ack_plan = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)
    last_seen_at = Column(DateTime, nullable=True)

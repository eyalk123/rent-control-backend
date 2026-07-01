from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.owner import Owner

# How stale last_seen_at may get before a "same claims" request triggers a write.
# Keeps the profile fresh without a DB write on every authenticated request.
LAST_SEEN_THROTTLE = timedelta(hours=1)


class OwnerRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, uid: str) -> Owner | None:
        return self.session.get(Owner, uid)

    def upsert(
        self,
        uid: str,
        email: str | None,
        display_name: str | None,
        picture_url: str | None,
    ) -> Owner:
        """Create or refresh the owner's profile from Firebase token claims.

        Throttled: writes only when the row is missing, a claim changed, or last_seen_at
        is stale (older than LAST_SEEN_THROTTLE). Otherwise returns the row untouched.
        """
        now = datetime.utcnow()
        owner = self.session.get(Owner, uid)

        if owner is None:
            owner = Owner(
                id=uid,
                email=email,
                display_name=display_name,
                picture_url=picture_url,
                last_seen_at=now,
            )
            self.session.add(owner)
            self.session.commit()
            self.session.refresh(owner)
            return owner

        claims_changed = (
            owner.email != email
            or owner.display_name != display_name
            or owner.picture_url != picture_url
        )
        last_seen_stale = owner.last_seen_at is None or (now - owner.last_seen_at) >= LAST_SEEN_THROTTLE

        if claims_changed or last_seen_stale:
            owner.email = email
            owner.display_name = display_name
            owner.picture_url = picture_url
            owner.last_seen_at = now
            self.session.commit()
            self.session.refresh(owner)

        return owner

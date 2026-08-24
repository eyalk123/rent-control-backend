import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.owner import Owner
from app.schemas.tour_state import MAX_ENTRIES

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

    # ── onboarding tour state ──────────────────────────────────────────────

    def get_tour_state(self, uid: str) -> dict:
        """The owner's onboarding progress, or empty defaults if they have no row yet."""
        owner = self.session.get(Owner, uid)
        return self._decode_tour_state(owner)

    def merge_tour_state(
        self,
        uid: str,
        tours_seen: dict[str, datetime] | None = None,
        seeds_shown: dict[str, datetime] | None = None,
        tours_disabled: bool | None = None,
        reset: bool = False,
    ) -> dict:
        """Merges a patch into the stored state and returns the result.

        Merge, not replace: the same account is routinely open on a phone and a browser,
        and a replace would let whichever wrote last silently drop the other's progress.
        Read-modify-write is safe enough here because entries are only ever *added* and
        the value is a timestamp — two racing writers converge on the same set.
        """
        owner = self.session.get(Owner, uid)
        if owner is None:
            return self._decode_tour_state(None)

        state = self._decode_tour_state(owner)

        if reset:
            state["tours_seen"] = {}
            state["seeds_shown"] = {}

        for field, incoming in (("tours_seen", tours_seen), ("seeds_shown", seeds_shown)):
            if not incoming:
                continue
            merged = dict(state[field])
            for key, when in incoming.items():
                # First sighting wins: re-showing a tour must not reset how long ago the
                # user first met it, which is the signal a later nudge would read.
                merged.setdefault(key, when.isoformat())
            state[field] = merged

        if tours_disabled is not None:
            state["tours_disabled"] = tours_disabled

        # Bound the row. Oldest entries go first; they are the ones least likely to still
        # gate anything, and the map is an optimisation, not a source of truth.
        for field in ("tours_seen", "seeds_shown"):
            if len(state[field]) > MAX_ENTRIES:
                keep = sorted(state[field].items(), key=lambda kv: kv[1])[-MAX_ENTRIES:]
                state[field] = dict(keep)

        owner.tour_state = json.dumps(state)
        self.session.commit()
        self.session.refresh(owner)
        return state

    @staticmethod
    def _decode_tour_state(owner: Owner | None) -> dict:
        """Never raises: a corrupt blob degrades to "seen nothing" rather than breaking
        every screen that asks whether to run a tour."""
        default = {"tours_seen": {}, "seeds_shown": {}, "tours_disabled": False}
        if owner is None or not owner.tour_state:
            return default
        try:
            parsed = json.loads(owner.tour_state)
        except (ValueError, TypeError):
            return default
        if not isinstance(parsed, dict):
            return default
        return {
            "tours_seen": parsed.get("tours_seen") or {},
            "seeds_shown": parsed.get("seeds_shown") or {},
            "tours_disabled": bool(parsed.get("tours_disabled", False)),
        }

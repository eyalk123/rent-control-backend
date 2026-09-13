from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OwnerRead(BaseModel):
    id: str  # Firebase UID
    email: str | None
    display_name: str | None
    picture_url: str | None
    # None means the signup country gate has not been answered yet — which is
    # exactly what the clients key that gate off. Every pre-existing account was
    # backfilled to IL, so it never fires for them.
    country: str | None = None
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None

    model_config = ConfigDict(from_attributes=True)

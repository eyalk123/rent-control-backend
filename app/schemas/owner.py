from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OwnerRead(BaseModel):
    id: str  # Firebase UID
    email: str | None
    display_name: str | None
    picture_url: str | None
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None

    model_config = ConfigDict(from_attributes=True)

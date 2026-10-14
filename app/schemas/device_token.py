
from pydantic import BaseModel, ConfigDict

from app.models.device_token import DevicePlatformEnum
from app.schemas.utc import UtcDatetime


class DeviceTokenCreate(BaseModel):
    token: str
    platform: DevicePlatformEnum
    locale: str | None = None


class DeviceTokenRead(BaseModel):
    id: int
    token: str
    platform: DevicePlatformEnum
    locale: str | None
    created_at: UtcDatetime
    last_used_at: UtcDatetime

    model_config = ConfigDict(from_attributes=True)

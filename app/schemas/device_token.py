from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.device_token import DevicePlatformEnum


class DeviceTokenCreate(BaseModel):
    token: str
    platform: DevicePlatformEnum


class DeviceTokenRead(BaseModel):
    id: int
    token: str
    platform: DevicePlatformEnum
    created_at: datetime
    last_used_at: datetime

    model_config = ConfigDict(from_attributes=True)

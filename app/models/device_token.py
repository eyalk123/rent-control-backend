import enum
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, Index, Integer, String

from app.models.base import Base


class DevicePlatformEnum(str, enum.Enum):
    IOS = "ios"
    ANDROID = "android"
    WEB = "web"


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False)
    token = Column(String, nullable=False, unique=True)
    platform = Column(
        Enum(DevicePlatformEnum, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    # App language for this device (e.g. "en", "he"). Nullable for legacy rows;
    # the reminder job falls back to English when unset.
    locale = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_device_tokens_owner_id", "owner_id"),
    )

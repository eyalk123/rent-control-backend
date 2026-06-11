from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.device_token import DevicePlatformEnum, DeviceToken


class DeviceTokenRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, token: str, owner_id: str, platform: DevicePlatformEnum) -> DeviceToken:
        """Register a token. A token is globally unique, so re-registering the same
        token (e.g. a new sign-in on the same device) re-points it at the current
        owner and refreshes ``last_used_at`` rather than creating a duplicate row."""
        existing = self.session.scalar(select(DeviceToken).where(DeviceToken.token == token))
        if existing is not None:
            existing.owner_id = owner_id
            existing.platform = platform
            existing.last_used_at = datetime.utcnow()
            self.session.commit()
            self.session.refresh(existing)
            return existing
        device_token = DeviceToken(token=token, owner_id=owner_id, platform=platform)
        self.session.add(device_token)
        self.session.commit()
        self.session.refresh(device_token)
        return device_token

    def delete_by_token(self, token: str, owner_id: str) -> bool:
        device_token = self.session.scalar(
            select(DeviceToken).where(
                DeviceToken.token == token,
                DeviceToken.owner_id == owner_id,
            )
        )
        if device_token is None:
            return False
        self.session.delete(device_token)
        self.session.commit()
        return True

    def delete_token(self, token: str) -> None:
        """Unconditionally drop a token (used to prune tokens Expo reports as dead)."""
        device_token = self.session.scalar(select(DeviceToken).where(DeviceToken.token == token))
        if device_token is not None:
            self.session.delete(device_token)
            self.session.commit()

    def list_by_owner(self, owner_id: str) -> list[DeviceToken]:
        stmt = select(DeviceToken).where(DeviceToken.owner_id == owner_id)
        return list(self.session.scalars(stmt).all())

    def list_distinct_owner_ids(self) -> list[str]:
        stmt = select(DeviceToken.owner_id).distinct()
        return list(self.session.scalars(stmt).all())

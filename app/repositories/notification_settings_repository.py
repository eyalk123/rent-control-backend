from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification_settings import NotificationSettings


class NotificationSettingsRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, owner_id: str) -> NotificationSettings | None:
        stmt = select(NotificationSettings).where(NotificationSettings.owner_id == owner_id)
        return self.session.scalar(stmt)

    def get_or_create(self, owner_id: str) -> NotificationSettings:
        settings = self.get(owner_id)
        if settings is None:
            settings = NotificationSettings(owner_id=owner_id)
            self.session.add(settings)
            self.session.commit()
            self.session.refresh(settings)
        return settings

    def update(self, owner_id: str, data: dict) -> NotificationSettings:
        settings = self.get_or_create(owner_id)
        for key, value in data.items():
            if value is not None and hasattr(settings, key):
                setattr(settings, key, value)
        self.session.commit()
        self.session.refresh(settings)
        return settings

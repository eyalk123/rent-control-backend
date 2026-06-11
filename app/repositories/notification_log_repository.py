from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification_log import NotificationLog, NotificationTypeEnum


class NotificationLogRepository:
    def __init__(self, session: Session):
        self.session = session

    def was_sent(
        self,
        owner_id: str,
        type: NotificationTypeEnum,
        entity_id: int,
        period_key: str,
    ) -> bool:
        stmt = select(NotificationLog.id).where(
            NotificationLog.owner_id == owner_id,
            NotificationLog.type == type,
            NotificationLog.entity_id == entity_id,
            NotificationLog.period_key == period_key,
        )
        return self.session.scalar(stmt) is not None

    def mark_sent(
        self,
        owner_id: str,
        type: NotificationTypeEnum,
        entity_id: int,
        period_key: str,
    ) -> NotificationLog:
        entry = NotificationLog(
            owner_id=owner_id,
            type=type,
            entity_id=entity_id,
            period_key=period_key,
        )
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry

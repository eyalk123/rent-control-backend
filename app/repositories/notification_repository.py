import json
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.notification import Notification, NotificationTypeEnum


class NotificationRepository:
    """The notification feed: one row per generated notification, carrying both
    push state (``pushed_at``) and in-app state (``read_at`` / ``dismissed_at``)."""

    def __init__(self, session: Session):
        self.session = session

    def was_generated(
        self,
        owner_id: str,
        type: NotificationTypeEnum,
        entity_id: int,
        period_key: str,
        offset: int,
    ) -> bool:
        stmt = select(Notification.id).where(
            Notification.owner_id == owner_id,
            Notification.type == type,
            Notification.entity_id == entity_id,
            Notification.period_key == period_key,
            Notification.offset == offset,
        )
        return self.session.scalar(stmt) is not None

    def create(
        self,
        owner_id: str,
        type: NotificationTypeEnum,
        entity_id: int,
        period_key: str,
        offset: int,
        data: dict | None = None,
    ) -> Notification:
        entry = Notification(
            owner_id=owner_id,
            type=type,
            entity_id=entity_id,
            period_key=period_key,
            offset=offset,
            data=json.dumps(data) if data is not None else None,
        )
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def list_for_owner(
        self, owner_id: str, include_dismissed: bool = False
    ) -> list[Notification]:
        stmt = select(Notification).where(Notification.owner_id == owner_id)
        if not include_dismissed:
            stmt = stmt.where(Notification.dismissed_at.is_(None))
        stmt = stmt.order_by(Notification.sent_at.desc())
        return list(self.session.scalars(stmt).all())

    def get_for_owner(self, notification_id: int, owner_id: str) -> Notification | None:
        stmt = select(Notification).where(
            Notification.id == notification_id,
            Notification.owner_id == owner_id,
        )
        return self.session.scalar(stmt)

    def mark_read(self, notification_id: int, owner_id: str) -> Notification | None:
        notification = self.get_for_owner(notification_id, owner_id)
        if notification is None:
            return None
        if notification.read_at is None:
            notification.read_at = datetime.utcnow()
            self.session.commit()
            self.session.refresh(notification)
        return notification

    def mark_all_read(self, owner_id: str) -> int:
        stmt = (
            update(Notification)
            .where(Notification.owner_id == owner_id, Notification.read_at.is_(None))
            .values(read_at=datetime.utcnow())
        )
        result = self.session.execute(stmt)
        self.session.commit()
        return result.rowcount or 0

    def dismiss(self, notification_id: int, owner_id: str) -> Notification | None:
        notification = self.get_for_owner(notification_id, owner_id)
        if notification is None:
            return None
        if notification.dismissed_at is None:
            notification.dismissed_at = datetime.utcnow()
            self.session.commit()
            self.session.refresh(notification)
        return notification

    def mark_pushed(self, notification_ids: list[int]) -> None:
        if not notification_ids:
            return
        stmt = (
            update(Notification)
            .where(Notification.id.in_(notification_ids))
            .values(pushed_at=datetime.utcnow())
        )
        self.session.execute(stmt)
        self.session.commit()

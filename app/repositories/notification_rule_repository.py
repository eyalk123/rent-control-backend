from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification import NotificationTypeEnum
from app.models.notification_rule import NotificationRule


class NotificationRuleRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_by_owner(self, owner_id: str) -> list[NotificationRule]:
        stmt = (
            select(NotificationRule)
            .where(NotificationRule.owner_id == owner_id)
            .order_by(NotificationRule.created_at.asc())
        )
        return list(self.session.scalars(stmt).all())

    def list_by_event(
        self, owner_id: str, event_type: NotificationTypeEnum, enabled_only: bool = False
    ) -> list[NotificationRule]:
        stmt = select(NotificationRule).where(
            NotificationRule.owner_id == owner_id,
            NotificationRule.event_type == event_type,
        )
        if enabled_only:
            stmt = stmt.where(NotificationRule.enabled.is_(True))
        return list(self.session.scalars(stmt).all())

    def get_for_owner(self, rule_id: int, owner_id: str) -> NotificationRule | None:
        stmt = select(NotificationRule).where(
            NotificationRule.id == rule_id,
            NotificationRule.owner_id == owner_id,
        )
        return self.session.scalar(stmt)

    def create(self, rule: NotificationRule) -> NotificationRule:
        self.session.add(rule)
        self.session.commit()
        self.session.refresh(rule)
        return rule

    def update(self, rule: NotificationRule, data: dict) -> NotificationRule:
        for key, value in data.items():
            if value is not None and hasattr(rule, key):
                setattr(rule, key, value)
        self.session.commit()
        self.session.refresh(rule)
        return rule

    def delete(self, rule: NotificationRule) -> None:
        self.session.delete(rule)
        self.session.commit()

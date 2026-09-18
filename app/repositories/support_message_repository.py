from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import utc_now_naive
from app.models.support_message import SupportMessage, SupportMessageTypeEnum


class SupportMessageRepository:
    def __init__(self, session: Session):
        self.session = session

    def count_since(self, owner_id: str, since: datetime) -> int:
        """How many submissions this owner has made since ``since``.

        Backs the hourly rate limit. Deliberately a database count rather than an
        in-process counter: several Railway replicas serve this route, and an
        in-memory window would let each one through separately.
        """
        stmt = (
            select(func.count())
            .select_from(SupportMessage)
            .where(
                SupportMessage.owner_id == owner_id,
                SupportMessage.created_at >= since,
            )
        )
        return int(self.session.scalar(stmt) or 0)

    def create(
        self,
        *,
        owner_id: str,
        type_: SupportMessageTypeEnum,
        message: str,
        screenshot_count: int,
        client_app: str | None,
        client_platform: str | None,
        client_version: str | None,
        language: str | None,
        country: str | None,
    ) -> SupportMessage:
        entry = SupportMessage(
            owner_id=owner_id,
            type=type_,
            message=message,
            screenshot_count=screenshot_count,
            client_app=client_app,
            client_platform=client_platform,
            client_version=client_version,
            language=language,
            country=country,
        )
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def mark_email_sent(self, entry: SupportMessage) -> SupportMessage:
        """Record a confirmed send. A row left null here is one the sender was asked
        to retry, and is the only trace of a submission we failed to deliver."""
        entry.email_sent_at = utc_now_naive()
        self.session.commit()
        self.session.refresh(entry)
        return entry

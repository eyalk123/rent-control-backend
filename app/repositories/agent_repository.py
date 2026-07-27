"""Data access for the Portfolio Chat Agent. All reads are owner-scoped."""
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent import AgentConversation, AgentMessage, AgentUsageLog


class AgentRepository:
    def __init__(self, session: Session):
        self.session = session

    # -- conversations --------------------------------------------------------------
    def create_conversation(self, owner_id: str, title: str | None) -> AgentConversation:
        convo = AgentConversation(owner_id=owner_id, title=title)
        self.session.add(convo)
        self.session.commit()
        self.session.refresh(convo)
        return convo

    def get_conversation(self, conversation_id: int, owner_id: str) -> AgentConversation | None:
        stmt = select(AgentConversation).where(
            AgentConversation.id == conversation_id,
            AgentConversation.owner_id == owner_id,
        )
        return self.session.scalar(stmt)

    def list_conversations(self, owner_id: str, limit: int = 50) -> list[AgentConversation]:
        stmt = (
            select(AgentConversation)
            .where(AgentConversation.owner_id == owner_id)
            .order_by(AgentConversation.updated_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt).all())

    def touch_conversation(self, convo: AgentConversation) -> None:
        convo.updated_at = datetime.utcnow()
        self.session.commit()

    # -- messages -------------------------------------------------------------------
    def add_message(self, conversation_id: int, role: str, content: str) -> AgentMessage:
        msg = AgentMessage(conversation_id=conversation_id, role=role, content=content)
        self.session.add(msg)
        self.session.commit()
        self.session.refresh(msg)
        return msg

    def list_messages(self, conversation_id: int) -> list[AgentMessage]:
        stmt = (
            select(AgentMessage)
            .where(AgentMessage.conversation_id == conversation_id)
            .order_by(AgentMessage.id)
        )
        return list(self.session.scalars(stmt).all())

    # -- usage / rate limiting ------------------------------------------------------
    def add_usage_log(self, log: AgentUsageLog) -> AgentUsageLog:
        self.session.add(log)
        self.session.commit()
        self.session.refresh(log)
        return log

    def count_messages_today(self, owner_id: str, today: date | None = None) -> int:
        """How many user messages the owner has sent since midnight UTC (one usage-log
        row per message). Backs the per-owner daily rate limit. The window is UTC because
        ``created_at`` is stored as ``datetime.utcnow()`` — mixing it with a local
        ``date.today()`` would reset the limit hours early near the date boundary."""
        start = datetime.combine(today or datetime.utcnow().date(), datetime.min.time())
        stmt = select(func.count(AgentUsageLog.id)).where(
            AgentUsageLog.owner_id == owner_id,
            AgentUsageLog.created_at >= start,
        )
        return int(self.session.scalar(stmt) or 0)

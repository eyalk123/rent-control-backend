"""Data access for the Portfolio Chat Agent. All reads are owner-scoped."""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import delete, func, select, update
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

    # -- deletion / retention -------------------------------------------------------
    # Deletes are explicit and ordered (children before parents) rather than relying on
    # DB ON DELETE CASCADE, so they behave the same on the SQLite test DB (which does not
    # enforce foreign keys by default) as on Postgres. Usage logs carry no message content
    # (owner_id, tokens, cost only): a single-conversation or retention delete DETACHES them
    # (conversation_id → NULL) to keep cost history; an account delete removes them.

    def delete_conversation(self, conversation_id: int, owner_id: str) -> bool:
        """Delete one conversation and its messages, owner-scoped. Returns False (→ 404) if
        it isn't this owner's. Usage logs are kept but detached from the conversation."""
        convo = self.get_conversation(conversation_id, owner_id)
        if convo is None:
            return False
        self.session.execute(
            delete(AgentMessage).where(AgentMessage.conversation_id == conversation_id)
        )
        self.session.execute(
            update(AgentUsageLog)
            .where(AgentUsageLog.conversation_id == conversation_id)
            .values(conversation_id=None)
        )
        self.session.execute(
            delete(AgentConversation).where(AgentConversation.id == conversation_id)
        )
        self.session.commit()
        return True

    def delete_owner_data(self, owner_id: str) -> None:
        """Remove ALL of an owner's agent data — conversations, their messages, and usage
        logs. Called from account deletion (the owner is gone, so nothing is retained).
        Does not commit; the caller commits as part of the account-deletion transaction."""
        convo_ids = list(
            self.session.scalars(
                select(AgentConversation.id).where(AgentConversation.owner_id == owner_id)
            ).all()
        )
        if convo_ids:
            self.session.execute(
                delete(AgentMessage).where(AgentMessage.conversation_id.in_(convo_ids))
            )
        self.session.execute(
            delete(AgentUsageLog).where(AgentUsageLog.owner_id == owner_id)
        )
        self.session.execute(
            delete(AgentConversation).where(AgentConversation.owner_id == owner_id)
        )

    def count_conversations_older_than(self, cutoff: datetime) -> int:
        """How many conversations retention would delete. For the dry run."""
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(AgentConversation)
                .where(AgentConversation.updated_at < cutoff)
            )
            or 0
        )

    def delete_conversations_older_than(self, cutoff: datetime) -> int:
        """Retention: delete every conversation (any owner) last updated before ``cutoff``,
        with its messages; detach its usage logs. Returns how many conversations were deleted."""
        convo_ids = list(
            self.session.scalars(
                select(AgentConversation.id).where(AgentConversation.updated_at < cutoff)
            ).all()
        )
        if not convo_ids:
            return 0
        self.session.execute(
            delete(AgentMessage).where(AgentMessage.conversation_id.in_(convo_ids))
        )
        self.session.execute(
            update(AgentUsageLog)
            .where(AgentUsageLog.conversation_id.in_(convo_ids))
            .values(conversation_id=None)
        )
        self.session.execute(
            delete(AgentConversation).where(AgentConversation.id.in_(convo_ids))
        )
        self.session.commit()
        return len(convo_ids)

    # -- usage / rate limiting ------------------------------------------------------
    def add_usage_log(self, log: AgentUsageLog) -> AgentUsageLog:
        self.session.add(log)
        self.session.commit()
        self.session.refresh(log)
        return log

    def create_pending_usage_log(
        self,
        owner_id: str,
        model: str,
        reserve_cost: float,
        conversation_id: Optional[int] = None,
    ) -> AgentUsageLog:
        """Reserve a usage-log row at the START of a turn: ``status='pending'`` with a
        provisional ``estimated_cost_usd`` so concurrent turns count against the caps
        before their real cost is known. Reconciled by ``finalize_usage_log`` at the end."""
        log = AgentUsageLog(
            owner_id=owner_id,
            conversation_id=conversation_id,
            model=model,
            status="pending",
            estimated_cost_usd=Decimal(str(reserve_cost)),
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            tool_calls_count=0,
        )
        return self.add_usage_log(log)

    def finalize_usage_log(
        self,
        log: AgentUsageLog,
        *,
        status: str,
        error_detail: Optional[str],
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_creation_tokens: int,
        estimated_cost_usd: Optional[Decimal],
        tool_calls_count: int,
    ) -> AgentUsageLog:
        """Reconcile a reserved row to the turn's real outcome (replaces the provisional
        reserve with the actual cost/tokens/status)."""
        log.status = status
        log.error_detail = error_detail
        log.latency_ms = latency_ms
        log.input_tokens = input_tokens
        log.output_tokens = output_tokens
        log.cache_read_tokens = cache_read_tokens
        log.cache_creation_tokens = cache_creation_tokens
        log.estimated_cost_usd = estimated_cost_usd
        log.tool_calls_count = tool_calls_count
        self.session.commit()
        self.session.refresh(log)
        return log

    def delete_usage_log(self, log: AgentUsageLog) -> None:
        """Drop a reservation whose request was rejected (over a cap), so it leaves no
        orphan ``pending`` row counting against the budget."""
        self.session.delete(log)
        self.session.commit()

    @staticmethod
    def _day_start(today: date | None) -> datetime:
        return datetime.combine(today or datetime.utcnow().date(), datetime.min.time())

    def count_messages_today(self, owner_id: str, today: date | None = None) -> int:
        """How many user messages the owner has sent since midnight UTC (one usage-log
        row per message, including in-flight ``pending`` reservations). Backs the per-owner
        daily message limit. The window is UTC because ``created_at`` is stored as
        ``datetime.utcnow()`` — mixing it with a local ``date.today()`` would reset the
        limit hours early near the date boundary."""
        stmt = select(func.count(AgentUsageLog.id)).where(
            AgentUsageLog.owner_id == owner_id,
            AgentUsageLog.created_at >= self._day_start(today),
        )
        return int(self.session.scalar(stmt) or 0)

    def sum_cost_today(self, owner_id: str, today: date | None = None) -> Decimal:
        """Owner's estimated spend since midnight UTC (finalized actuals + pending
        reservations). Backs the per-owner daily cost cap."""
        stmt = select(func.coalesce(func.sum(AgentUsageLog.estimated_cost_usd), 0)).where(
            AgentUsageLog.owner_id == owner_id,
            AgentUsageLog.created_at >= self._day_start(today),
        )
        return Decimal(str(self.session.scalar(stmt) or 0))

    def sum_cost_today_global(self, today: date | None = None) -> Decimal:
        """App-wide estimated spend since midnight UTC (all owners). Backs the global
        daily cost kill-switch."""
        stmt = select(func.coalesce(func.sum(AgentUsageLog.estimated_cost_usd), 0)).where(
            AgentUsageLog.created_at >= self._day_start(today),
        )
        return Decimal(str(self.session.scalar(stmt) or 0))

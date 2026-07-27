"""Portfolio Chat Agent persistence.

The Anthropic Messages API is stateless — every turn we resend the full history,
including the tool_use / tool_result blocks. So we store each message's content
verbatim (as JSON) and replay it. One ``agent_usage_log`` row is written per user
message for cost accounting and the daily rate limit.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text

from app.models.base import Base


class AgentConversation(Base):
    """A chat thread. Scoped to one owner; a landlord can only ever see their own."""

    __tablename__ = "agent_conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentMessage(Base):
    """One turn in a conversation. ``content`` is the JSON of the Anthropic message
    content — a plain string for a user turn, or the list of content blocks
    (text / tool_use / tool_result) for assistant and tool-result turns — stored so
    the exact history can be replayed to the model."""

    __tablename__ = "agent_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(
        Integer, ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role = Column(String, nullable=False)  # user | assistant
    content = Column(Text, nullable=False)  # JSON (string or list of blocks)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AgentUsageLog(Base):
    """Telemetry for one user message end-to-end (which may span several model calls
    and tool round-trips). Backs cost reporting and the per-owner daily rate limit."""

    __tablename__ = "agent_usage_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    conversation_id = Column(
        Integer, ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    model = Column(String, nullable=True)
    status = Column(String, nullable=False)  # success | refusal | error
    error_detail = Column(Text, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    cache_read_tokens = Column(Integer, nullable=True)
    cache_creation_tokens = Column(Integer, nullable=True)
    estimated_cost_usd = Column(Numeric(10, 6), nullable=True)
    tool_calls_count = Column(Integer, nullable=False, default=0)

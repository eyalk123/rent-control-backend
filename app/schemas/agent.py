from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentChatRequest(BaseModel):
    # Cap the length so a huge paste can't blow up token cost / context (it is stored
    # verbatim and re-sent to the model every loop iteration). 4000 chars is far more
    # than any real question needs.
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: Optional[int] = None


class AgentStatusResponse(BaseModel):
    #: Is an Anthropic key configured on this deployment. Unchanged meaning — a client
    #: that only understands this field keeps working.
    enabled: bool
    #: Does this account's plan include the assistant. Separate from `enabled` on
    #: purpose: folding them into one flag would hide the feature from free accounts
    #: entirely, and a feature nobody can see is a feature nobody upgrades for.
    entitled: bool = True
    #: The cheapest plan that includes it, when this account's does not.
    required_plan: Optional[str] = None


class ConversationRead(BaseModel):
    id: int
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

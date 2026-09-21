"""Portfolio Chat Agent endpoints.

- ``GET  /agent/status``          — is the agent configured? (clients hide the UI if not)
- ``POST /agent/chat``            — ask a question; response streams back as SSE
- ``GET  /agent/conversations``   — the owner's recent chat threads
- ``GET  /agent/conversations/{id}`` — one thread's message history

Everything is owner-scoped via ``current_user["user_id"]``; the model never receives
or influences the owner id.
"""
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.dependencies import (
    get_agent_service,
    get_current_user,
    get_entitlement_gate,
)
from app.services import entitlement_service as ent
from app.services.entitlement_gate import EntitlementGate
from app.schemas.agent import AgentChatRequest, AgentStatusResponse, ConversationRead
from app.services.agent_service import AgentService

router = APIRouter()


def _sse(event: dict) -> str:
    """One Server-Sent Event: a named event plus its JSON payload."""
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.get("/status", response_model=AgentStatusResponse)
def agent_status(
    current_user: Annotated[dict, Depends(get_current_user)],
    service: Annotated[AgentService, Depends(get_agent_service)],
    entitlement_gate: Annotated[EntitlementGate, Depends(get_entitlement_gate)],
):
    """Whether the agent is available to this account, and why not if it isn't.

    `enabled` keeps its original meaning — is an Anthropic key configured on this
    deployment — and `entitled` is the separate, per-account question of whether the plan
    includes it. Two fields rather than one AND: folding them together would hide the
    assistant from free accounts entirely, and a feature nobody can see is a feature
    nobody upgrades for. Clients show it locked instead.
    """
    state = entitlement_gate.state_for(current_user["user_id"])
    # While enforcement is off nobody is restricted, so nobody is told to upgrade —
    # `required_plan` must follow `entitled`, not the plan, or a client would render an
    # upgrade prompt for a restriction that is not being applied.
    entitled = state.plan.agent or not state.enforced
    return AgentStatusResponse(
        enabled=service.enabled,
        entitled=entitled,
        required_plan=None if entitled else ent.PLANS[1].plan,
    )


@router.post("/chat")
def chat(
    request: AgentChatRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: Annotated[AgentService, Depends(get_agent_service)],
    entitlement_gate: Annotated[EntitlementGate, Depends(get_entitlement_gate)],
):
    """Ask the agent a question. The answer streams back as text/event-stream with
    ``conversation`` / ``tool`` / ``text`` / ``done`` / ``error`` events."""
    owner_id = current_user["user_id"]
    if not service.enabled:
        raise HTTPException(status_code=503, detail="The portfolio agent is not configured.")
    # Enforced here as well as hidden in the clients: a hidden button is a courtesy, not
    # a gate, and this endpoint costs real money per call.
    entitlement_gate.require_agent(owner_id)

    # start() reserves the turn's budget, enforces the daily limits (message count + per-owner
    # and global cost caps → 429), persists the user turn, and validates access — all
    # synchronously (503/404/429) before streaming begins, so they surface as real HTTP errors
    # rather than mid-stream events.
    conversation_id, events = service.start(owner_id, request.conversation_id, request.message)

    def event_source():
        yield _sse({"type": "conversation", "conversation_id": conversation_id})
        for event in events:
            yield _sse(event)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        # Edge proxies (Railway/nginx) buffer responses by default, which defeats
        # streaming — the answer would arrive all at once. These headers opt out.
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations", response_model=list[ConversationRead])
def list_conversations(
    current_user: Annotated[dict, Depends(get_current_user)],
    service: Annotated[AgentService, Depends(get_agent_service)],
):
    return service.repo.list_conversations(current_user["user_id"])


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: Annotated[AgentService, Depends(get_agent_service)],
):
    owner_id = current_user["user_id"]
    convo = service.repo.get_conversation(conversation_id, owner_id)
    if convo is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = service.repo.list_messages(conversation_id)
    return {
        "conversation": ConversationRead.model_validate(convo).model_dump(mode="json"),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": json.loads(m.content),
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    current_user: Annotated[dict, Depends(get_current_user)],
    service: Annotated[AgentService, Depends(get_agent_service)],
):
    """Delete one of the owner's conversations (and its stored messages). 404 if it isn't
    theirs — never reveals another tenant's row."""
    deleted = service.repo.delete_conversation(conversation_id, current_user["user_id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"success": True}

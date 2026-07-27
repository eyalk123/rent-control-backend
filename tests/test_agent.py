"""API tests for the /agent endpoints (agent service stubbed — no network)."""
from app.api.dependencies import get_agent_service
from app.config import settings
from app.main import app
from app.models.agent import AgentUsageLog
from app.services.agent_service import AgentService
from tests.conftest import OWNER_A, OWNER_B
from tests.unit.test_agent_service import FakeAnthropic, FakeResponse, FakeText


def _use_agent(db_session, script=None, api_key="test-key"):
    """Override the agent-service dependency with one backed by a stubbed Anthropic client."""
    client = FakeAnthropic(script or []) if api_key else None
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        db_session, api_key=api_key, model="claude-sonnet-4-6", client=client
    )


def _parse_sse(body: str) -> list[str]:
    return [line[len("event: "):] for line in body.splitlines() if line.startswith("event: ")]


def test_status_reports_enabled(client, db_session):
    _use_agent(db_session, api_key="test-key")
    assert client.get("/agent/status").json() == {"enabled": True}


def test_status_reports_disabled_without_key(client, db_session):
    _use_agent(db_session, api_key="")  # no key, no injected client
    assert client.get("/agent/status").json() == {"enabled": False}


def test_chat_streams_sse(client, db_session):
    _use_agent(db_session, [FakeResponse([FakeText("Hi there.")], "end_turn")])
    resp = client.post("/agent/chat", json={"message": "hello"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)
    assert events[0] == "conversation"
    assert "text" in events
    assert events[-1] == "done"
    assert "Hi there." in resp.text


def test_chat_disabled_returns_503(client, db_session):
    _use_agent(db_session, api_key="")
    assert client.post("/agent/chat", json={"message": "hi"}).status_code == 503


def test_chat_rate_limited_returns_429(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_DAILY_MESSAGE_LIMIT", 1)
    # One usage-log row already today == at the limit.
    db_session.add(AgentUsageLog(owner_id=OWNER_A, status="success", tool_calls_count=0))
    db_session.commit()
    _use_agent(db_session, [FakeResponse([FakeText("x")], "end_turn")])

    assert client.post("/agent/chat", json={"message": "hi"}).status_code == 429


def test_chat_requires_message(client, db_session):
    _use_agent(db_session, [FakeResponse([FakeText("x")], "end_turn")])
    assert client.post("/agent/chat", json={"message": ""}).status_code == 422


def test_conversations_list_and_detail(client, db_session):
    _use_agent(db_session, [FakeResponse([FakeText("Answer.")], "end_turn")])
    convo_id = None
    resp = client.post("/agent/chat", json={"message": "a question"})
    for line in resp.text.splitlines():
        if line.startswith("data: ") and '"conversation_id"' in line:
            import json
            convo_id = json.loads(line[len("data: "):])["conversation_id"]
            break

    listing = client.get("/agent/conversations").json()
    assert len(listing) == 1 and listing[0]["id"] == convo_id

    detail = client.get(f"/agent/conversations/{convo_id}").json()
    assert detail["conversation"]["id"] == convo_id
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["messages"][0]["content"] == "a question"


def test_conversation_detail_cross_owner_404(client_factory, db_session):
    _use_agent(db_session, [FakeResponse([FakeText("Answer.")], "end_turn")])
    a = client_factory(OWNER_A)
    resp = a.post("/agent/chat", json={"message": "mine"})
    import json
    convo_id = next(
        json.loads(line[len("data: "):])["conversation_id"]
        for line in resp.text.splitlines()
        if line.startswith("data: ") and '"conversation_id"' in line
    )

    b = client_factory(OWNER_B)
    assert b.get(f"/agent/conversations/{convo_id}").status_code == 404

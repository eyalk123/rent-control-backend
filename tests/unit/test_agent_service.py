"""Unit tests for the agent loop, with a stubbed Anthropic client (no network).

The MODEL is faked, but the TOOLS run for real against the test DB — so these also
prove the model→tool→result→model loop wires up end to end.
"""
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.services.agent_service import _REFUSAL_TEXT, SYSTEM_PROMPT, AgentService
from tests.conftest import OWNER_A, OWNER_B
from tests.factories import make_property, make_renter

_TODAY = date.today()
_ACTIVE = dict(lease_start=_TODAY - timedelta(days=30), lease_end=_TODAY + timedelta(days=335))


# --- fakes mimicking the Anthropic SDK response shape ------------------------------

class FakeText:
    type = "text"

    def __init__(self, text):
        self.text = text

    def model_dump(self):
        return {"type": "text", "text": self.text}


class FakeToolUse:
    type = "tool_use"

    def __init__(self, id, name, input):
        self.id = id
        self.name = name
        self.input = input

    def model_dump(self):
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


class FakeUsage:
    def __init__(self, i=10, o=5):
        self.input_tokens = i
        self.output_tokens = o
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class FakeResponse:
    def __init__(self, content, stop_reason, usage=None):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage or FakeUsage()


class FakeStream:
    """Context manager mimicking client.messages.stream(...)."""

    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        for block in self._response.content:
            if getattr(block, "type", None) == "text":
                yield block.text

    def get_final_message(self):
        return self._response


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def stream(self, **kwargs):
        # Snapshot the message list membership at call time (the loop mutates it in place).
        self.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeStream(item)


class FakeAnthropic:
    def __init__(self, script):
        self.messages = FakeMessages(script)


def _service(db_session, script):
    return AgentService(
        db_session, api_key="test-key", model="claude-sonnet-4-6", client=FakeAnthropic(script)
    )


def test_single_tool_turn_end_to_end(db_session):
    prop = make_property(db_session, property_owner="Dad")
    make_renter(db_session, property_id=prop.id, first_name="Late", last_name="Payer",
                payment_day_of_month=1, **_ACTIVE)

    svc = _service(db_session, [
        FakeResponse([FakeToolUse("tu1", "get_overdue", {})], "tool_use"),
        FakeResponse([FakeText("You have 1 overdue renter: Late Payer (renter 1).")], "end_turn"),
    ])
    result = svc.send_message(OWNER_A, None, "who hasn't paid this month?")

    assert result["status"] == "success"
    assert "Late Payer" in result["message"]
    assert result["tool_calls"] == ["get_overdue"]

    # The loop made two model calls; the 2nd fed the tool_result back as a user turn.
    calls = svc._injected_client.messages.calls
    assert len(calls) == 2
    assert calls[1]["messages"][-1]["content"][0]["type"] == "tool_result"

    # Full exchange persisted: user, assistant(tool_use), user(tool_result), assistant(text).
    msgs = svc.repo.list_messages(result["conversation_id"])
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    # One usage-log row (the daily rate-limit counter).
    assert svc.repo.count_messages_today(OWNER_A) == 1


def test_multi_tool_turn(db_session):
    make_property(db_session, property_owner="Dad")
    svc = _service(db_session, [
        FakeResponse([FakeToolUse("t1", "list_properties", {})], "tool_use"),
        FakeResponse([FakeToolUse("t2", "get_report_summary",
                                  {"type": "income_expense", "year": _TODAY.year})], "tool_use"),
        FakeResponse([FakeText("Net for the year is ₪0.")], "end_turn"),
    ])
    result = svc.send_message(OWNER_A, None, "net profit this year?")

    assert result["tool_calls"] == ["list_properties", "get_report_summary"]
    assert len(svc._injected_client.messages.calls) == 3


def test_refusal_stop_reason(db_session):
    svc = _service(db_session, [FakeResponse([], "refusal")])
    result = svc.send_message(OWNER_A, None, "do something disallowed")
    assert result["status"] == "refusal"
    assert result["message"] == _REFUSAL_TEXT


def test_conversation_continuity(db_session):
    svc = _service(db_session, [
        FakeResponse([FakeText("Hello.")], "end_turn"),
        FakeResponse([FakeText("Second answer.")], "end_turn"),
    ])
    first = svc.send_message(OWNER_A, None, "hi")
    convo_id = first["conversation_id"]
    second = svc.send_message(OWNER_A, convo_id, "follow up")

    assert second["conversation_id"] == convo_id
    # The 2nd turn replays the prior history (more messages than the 1st turn saw).
    calls = svc._injected_client.messages.calls
    assert len(calls[1]["messages"]) > len(calls[0]["messages"])
    # Four messages total on one conversation.
    assert len(svc.repo.list_messages(convo_id)) == 4


def test_conversation_is_owner_scoped(db_session):
    svc = _service(db_session, [
        FakeResponse([FakeText("ok")], "end_turn"),
        FakeResponse([FakeText("ok")], "end_turn"),
    ])
    convo_id = svc.send_message(OWNER_A, None, "hi")["conversation_id"]

    # OWNER_B cannot post into OWNER_A's conversation.
    with pytest.raises(HTTPException) as exc:
        svc.send_message(OWNER_B, convo_id, "let me in")
    assert exc.value.status_code == 404


def test_stream_emits_tool_then_text_then_done(db_session):
    prop = make_property(db_session, property_owner="Dad")
    make_renter(db_session, property_id=prop.id, first_name="Late", last_name="Payer",
                payment_day_of_month=1, **_ACTIVE)
    svc = _service(db_session, [
        FakeResponse([FakeToolUse("tu1", "get_overdue", {})], "tool_use"),
        FakeResponse([FakeText("One overdue: "), FakeText("Late Payer.")], "end_turn"),
    ])

    _, events = svc.start(OWNER_A, None, "who is late?")
    seq = list(events)
    types = [e["type"] for e in seq]

    # Activity event first, then streamed text deltas, then a terminal done.
    assert types[0] == "tool" and seq[0]["name"] == "get_overdue"
    assert "text" in types
    assert types[-1] == "done"
    text = "".join(e["delta"] for e in seq if e["type"] == "text")
    assert text == "One overdue: Late Payer."  # two deltas concatenated
    done = seq[-1]
    assert done["status"] == "success"
    assert done["tool_calls"] == ["get_overdue"]


def test_stream_upstream_error_becomes_error_event(db_session):
    svc = _service(db_session, [RuntimeError("connection reset")])
    _, events = svc.start(OWNER_A, None, "hi")
    seq = list(events)
    assert seq[-1]["type"] == "error"
    # And a usage-log row records the failure.
    assert svc.repo.count_messages_today(OWNER_A) == 1


def test_system_prompt_encodes_guardrails():
    """Regression guard: the non-negotiable behaviours must stay in the system prompt."""
    p = SYSTEM_PROMPT.lower()
    # Never compute — always use tools.
    assert "never" in p and "tool" in p
    # Legal/tax guardrail with the professional referral.
    assert "legal" in p and "tax" in p and "consult" in p
    # Language mirroring (answer in the user's language, not the app setting).
    assert "language of the user" in p
    # Prompt-injection defence for tool-result data.
    assert "data, not instructions" in p
    # Money convention.
    assert "₪" in SYSTEM_PROMPT
    # Tappable-source marker convention the web client parses into chips.
    assert "[[type:id|label]]" in SYSTEM_PROMPT


def test_disabled_when_no_api_key(db_session):
    svc = AgentService(db_session, api_key="", model="claude-sonnet-4-6", client=None)
    assert svc.enabled is False
    with pytest.raises(HTTPException) as exc:
        svc.send_message(OWNER_A, None, "hi")
    assert exc.value.status_code == 503
    # Nothing was persisted on the disabled path.
    assert svc.repo.count_messages_today(OWNER_A) == 0

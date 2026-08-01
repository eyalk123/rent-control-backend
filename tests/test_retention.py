"""The retention sweep: POST /internal/run-retention.

The behaviour that matters most is the dry run. Turning a window on for the first time deletes
everything already older than it, which on a live database can be a lot — so `dry_run` has to
be trustworthy about counting without touching anything.
"""
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import settings
from app.models.activity_log import ActivityLog
from app.models.agent import AgentConversation, AgentMessage, AgentUsageLog
from app.models.notification import Notification, NotificationTypeEnum
from tests.conftest import OWNER_A

SECRET = "s3cret"


def _headers():
    return {"X-Cron-Secret": SECRET}


def _seed(db_session, *, age_days: int) -> None:
    """One row of each swept class, aged by `age_days`."""
    when = datetime.utcnow() - timedelta(days=age_days)
    convo = AgentConversation(owner_id=OWNER_A, title="c", updated_at=when)
    db_session.add(convo)
    db_session.commit()
    db_session.refresh(convo)
    db_session.add_all([
        AgentMessage(conversation_id=convo.id, role="user", content='"hi"'),
        AgentUsageLog(owner_id=OWNER_A, status="success", conversation_id=convo.id, tool_calls_count=0),
        ActivityLog(
            owner_id=OWNER_A, action="delete", entity_type="renter", entity_id=1,
            label="שרה כהן", created_at=when,
        ),
        Notification(
            owner_id=OWNER_A, type=NotificationTypeEnum.OVERDUE, entity_id=1,
            period_key="2025-03", sent_at=when,
        ),
    ])
    db_session.commit()


def _enable(monkeypatch, agent=90, activity=365, notifications=365):
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", SECRET)
    monkeypatch.setattr(settings, "AGENT_RETENTION_DAYS", agent)
    monkeypatch.setattr(settings, "ACTIVITY_LOG_RETENTION_DAYS", activity)
    monkeypatch.setattr(settings, "NOTIFICATION_RETENTION_DAYS", notifications)


def test_requires_the_cron_secret(client, monkeypatch):
    _enable(monkeypatch)
    assert client.post("/internal/run-retention").status_code == 401


def test_sweeps_every_class_past_its_window(client, db_session, monkeypatch):
    _seed(db_session, age_days=400)
    _enable(monkeypatch)

    body = client.post("/internal/run-retention", headers=_headers()).json()

    assert body["swept"] == {"agent_conversations": 1, "activity_log": 1, "notifications": 1}
    assert body["disabled"] == []
    db_session.expire_all()
    assert db_session.scalars(select(AgentConversation)).all() == []
    assert db_session.scalars(select(AgentMessage)).all() == []
    assert db_session.scalars(select(ActivityLog)).all() == []
    assert db_session.scalars(select(Notification)).all() == []


def test_leaves_data_inside_its_window_alone(client, db_session, monkeypatch):
    _seed(db_session, age_days=30)  # newer than every window
    _enable(monkeypatch)

    body = client.post("/internal/run-retention", headers=_headers()).json()

    assert body["swept"] == {"agent_conversations": 0, "activity_log": 0, "notifications": 0}
    db_session.expire_all()
    assert len(db_session.scalars(select(AgentConversation)).all()) == 1
    assert len(db_session.scalars(select(ActivityLog)).all()) == 1


def test_windows_are_independent(client, db_session, monkeypatch):
    """A 100-day-old row is past the 90-day chat window but inside the 365-day ones."""
    _seed(db_session, age_days=100)
    _enable(monkeypatch)

    body = client.post("/internal/run-retention", headers=_headers()).json()

    assert body["swept"] == {"agent_conversations": 1, "activity_log": 0, "notifications": 0}
    db_session.expire_all()
    assert db_session.scalars(select(AgentConversation)).all() == []
    assert len(db_session.scalars(select(ActivityLog)).all()) == 1


def test_dry_run_counts_without_deleting(client, db_session, monkeypatch):
    _seed(db_session, age_days=400)
    _enable(monkeypatch)

    body = client.post("/internal/run-retention?dry_run=true", headers=_headers()).json()

    assert body["dry_run"] is True
    assert body["swept"] == {"agent_conversations": 1, "activity_log": 1, "notifications": 1}
    db_session.expire_all()
    assert len(db_session.scalars(select(AgentConversation)).all()) == 1
    assert len(db_session.scalars(select(AgentMessage)).all()) == 1
    assert len(db_session.scalars(select(ActivityLog)).all()) == 1
    assert len(db_session.scalars(select(Notification)).all()) == 1


def test_usage_logs_survive_and_are_detached(client, db_session, monkeypatch):
    """Cost history has no PII and is worth keeping — it outlives the conversation."""
    _seed(db_session, age_days=400)
    _enable(monkeypatch)

    client.post("/internal/run-retention", headers=_headers())

    db_session.expire_all()
    logs = db_session.scalars(select(AgentUsageLog)).all()
    assert len(logs) == 1
    assert logs[0].conversation_id is None


def test_disabled_classes_are_named_not_silently_skipped(client, db_session, monkeypatch):
    _seed(db_session, age_days=400)
    _enable(monkeypatch, agent=0, activity=0, notifications=365)

    body = client.post("/internal/run-retention", headers=_headers()).json()

    assert sorted(body["disabled"]) == ["activity_log", "agent_conversations"]
    assert body["swept"] == {"notifications": 1}
    db_session.expire_all()
    assert len(db_session.scalars(select(AgentConversation)).all()) == 1
    assert len(db_session.scalars(select(ActivityLog)).all()) == 1

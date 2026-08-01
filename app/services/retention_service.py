"""Age out data that has outlived its usefulness.

One sweep covering every class, so there is a single job to schedule and a single place to
reason about what is kept. Each class has its own window; `0` disables that class.

Nothing here is self-scheduling — an external scheduler must call
`POST /internal/run-retention`. A window without a scheduler deletes nothing, and a scheduler
without a window deletes nothing; the result reports which classes are disabled so that state
is visible rather than looking like success.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.activity_log import ActivityLog
from app.models.notification import Notification
from app.repositories.agent_repository import AgentRepository

logger = logging.getLogger(__name__)


@dataclass
class RetentionResult:
    dry_run: bool
    swept: dict[str, int] = field(default_factory=dict)
    disabled: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "status": "ok",
            "dry_run": self.dry_run,
            "swept": self.swept,
            "disabled": self.disabled,
        }


def _cutoff(days: int) -> datetime:
    return datetime.utcnow() - timedelta(days=days)


class RetentionService:
    def __init__(self, db: Session):
        self.db = db

    def run(self, dry_run: bool = False) -> RetentionResult:
        """Sweep every class. With `dry_run`, count what would go and delete nothing —
        worth doing before enabling a window for the first time, because the first real run
        deletes everything already older than it."""
        result = RetentionResult(dry_run=dry_run)

        self._sweep_agent_conversations(result, dry_run)
        self._sweep_activity_log(result, dry_run)
        self._sweep_notifications(result, dry_run)

        if dry_run:
            self.db.rollback()
        else:
            self.db.commit()

        if result.disabled:
            logger.warning(
                "Retention ran with these classes disabled (window is 0): %s",
                ", ".join(result.disabled),
            )
        return result

    def _sweep_agent_conversations(self, result: RetentionResult, dry_run: bool) -> None:
        days = settings.AGENT_RETENTION_DAYS
        if days <= 0:
            result.disabled.append("agent_conversations")
            return

        repo = AgentRepository(self.db)
        cutoff = _cutoff(days)
        if dry_run:
            result.swept["agent_conversations"] = repo.count_conversations_older_than(cutoff)
            return
        # Detaches usage logs rather than deleting them: cost history has no PII and is
        # worth keeping.
        result.swept["agent_conversations"] = repo.delete_conversations_older_than(cutoff)

    def _sweep_activity_log(self, result: RetentionResult, dry_run: bool) -> None:
        self._sweep_by_column(
            result,
            dry_run,
            name="activity_log",
            model=ActivityLog,
            column=ActivityLog.created_at,
            days=settings.ACTIVITY_LOG_RETENTION_DAYS,
        )

    def _sweep_notifications(self, result: RetentionResult, dry_run: bool) -> None:
        self._sweep_by_column(
            result,
            dry_run,
            name="notifications",
            model=Notification,
            column=Notification.sent_at,
            days=settings.NOTIFICATION_RETENTION_DAYS,
        )

    def _sweep_by_column(
        self, result: RetentionResult, dry_run: bool, *, name: str, model, column, days: int
    ) -> None:
        if days <= 0:
            result.disabled.append(name)
            return

        cutoff = _cutoff(days)
        count = int(
            self.db.scalar(select(func.count()).select_from(model).where(column < cutoff)) or 0
        )
        if not dry_run and count:
            self.db.execute(delete(model).where(column < cutoff))
        result.swept[name] = count

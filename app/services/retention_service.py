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
from datetime import date, datetime, timedelta

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.activity_log import ActivityLog
from app.models.notification import Notification, NotificationTypeEnum
from app.models.renter import Renter
from app.repositories.agent_repository import AgentRepository
from app.repositories.renter_repository import effective_lease_end

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


def _cpi_notification_for_an_active_renter():
    """A `cpi_rent_change` row whose renter's lease is still running, as a SQL predicate.

    `Notification.entity_id` holds the renter id but carries no foreign key, so this is a
    correlated EXISTS rather than a join, and it is only meaningful together with the type
    check — `entity_id` is interpretable only through `type`. "Still running" reuses
    `effective_lease_end` from the renter repository so there is one definition of an
    active lease, not a copy of it here.
    """
    today = date.today()
    return and_(
        Notification.type == NotificationTypeEnum.CPI_RENT_CHANGE,
        select(Renter.id)
        .where(
            Renter.id == Notification.entity_id,
            Renter.lease_start <= today,
            effective_lease_end() >= today,
        )
        .exists(),
    )


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
        """One exception to the window: a `cpi_rent_change` notification outlives it while
        the lease it is about is still running.

        A CPI notification is the only point-in-time record of the figure the owner was
        actually shown — the calculation behind it is not reproducible once a cached index
        reading is superseded or a lease is edited. Leases routinely run longer than a
        year, so the plain window deletes the evidence while the lease it belongs to is
        still live. Once the lease has ended the row ages out on the normal window like
        any other; nothing is kept forever.
        """
        self._sweep_by_column(
            result,
            dry_run,
            name="notifications",
            model=Notification,
            column=Notification.sent_at,
            days=settings.NOTIFICATION_RETENTION_DAYS,
            extra_where=~_cpi_notification_for_an_active_renter(),
        )

    def _sweep_by_column(
        self,
        result: RetentionResult,
        dry_run: bool,
        *,
        name: str,
        model,
        column,
        days: int,
        extra_where=None,
    ) -> None:
        """`extra_where` narrows what the window is allowed to take, and is ANDed into
        both the count and the delete so a dry run reports exactly what a real run does."""
        if days <= 0:
            result.disabled.append(name)
            return

        cutoff = _cutoff(days)
        condition = column < cutoff
        if extra_where is not None:
            condition = and_(condition, extra_where)
        count = int(
            self.db.scalar(select(func.count()).select_from(model).where(condition)) or 0
        )
        if not dry_run and count:
            self.db.execute(delete(model).where(condition))
        result.swept[name] = count

import json
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job_run import JobRun

# Statuses that mean "the job did its work". ``degraded`` counts: a CPI refresh served
# by the Bank of Israel fallback produced correct readings, so anything waiting on that
# work can proceed. ``stale`` does not — the cache is further behind than allowed, which
# is precisely the case where downstream work would be based on nothing.
SUCCESS_STATUSES = ("ok", "degraded")


class JobRunRepository:
    def __init__(self, session: Session):
        self.session = session

    def start(self, job_name: str) -> JobRun:
        """Open a run row before the work begins.

        Written up front, and flushed rather than committed, so the row participates in
        the job's own transaction. A process killed mid-run therefore leaves either
        nothing or a row with a NULL ``finished_at`` — both of which read as "did not
        complete", which is the honest answer.
        """
        run = JobRun(job_name=job_name, started_at=datetime.utcnow(), status="running")
        self.session.add(run)
        self.session.flush()
        return run

    def finish(
        self,
        run: JobRun,
        status: str,
        summary: Optional[dict[str, Any]] = None,
    ) -> JobRun:
        run.finished_at = datetime.utcnow()
        run.status = status
        run.summary = json.dumps(summary, default=str) if summary is not None else None
        self.session.commit()
        return run

    def fail(self, job_name: str, started_at: datetime, error: str) -> JobRun:
        """Record a run that raised.

        The open row from :meth:`start` cannot simply be updated: the exception may have
        left the session with a failed transaction, and it is rolled back here — which
        also discards that pending row. So the failure is written as a fresh insert,
        carrying the original ``started_at`` so the duration is still right.
        """
        self.session.rollback()
        run = JobRun(
            job_name=job_name,
            started_at=started_at,
            finished_at=datetime.utcnow(),
            status="failed",
            error=error,
        )
        self.session.add(run)
        self.session.commit()
        return run

    def last_run(self, job_name: str) -> Optional[JobRun]:
        """The most recent attempt, successful or not."""
        stmt = (
            select(JobRun)
            .where(JobRun.job_name == job_name)
            .order_by(JobRun.started_at.desc(), JobRun.id.desc())
            .limit(1)
        )
        return self.session.scalar(stmt)

    def last_success(self, job_name: str) -> Optional[JobRun]:
        """The most recent run that finished having done its work."""
        stmt = (
            select(JobRun)
            .where(JobRun.job_name == job_name, JobRun.status.in_(SUCCESS_STATUSES))
            .order_by(JobRun.started_at.desc(), JobRun.id.desc())
            .limit(1)
        )
        return self.session.scalar(stmt)

    def succeeded_on(self, job_name: str, day: date) -> bool:
        """Whether ``job_name`` has a successful run dated ``day``.

        Compared on the UTC calendar date rather than an elapsed-hours window because
        the jobs are daily and the question being asked is "has today's run happened
        yet", not "how long since the last one".
        """
        run = self.last_success(job_name)
        return run is not None and run.started_at.date() >= day

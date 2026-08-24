import logging
import secrets
from datetime import date
from typing import Annotated, Any, Callable

import sentry_sdk
from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse

from app.api.dependencies import (
    get_cpi_indexing_service,
    get_job_run_repository,
    get_reminder_service,
    get_retention_service,
)
from app.config import settings
from app.repositories.job_run_repository import JobRunRepository
from app.services.cpi_indexing_service import CpiIndexingService
from app.services.reminder_service import ReminderService
from app.services.retention_service import RetentionService

logger = logging.getLogger(__name__)

router = APIRouter()

# Job names as they appear in job_runs.job_name. Constants because the reminders job
# looks up the indexing job by name, and a typo there would silently mean "never ran".
JOB_REMINDERS = "reminders"
JOB_CPI_INDEXING = "cpi_indexing"
JOB_RETENTION = "retention"


def verify_cron_secret(x_cron_secret: Annotated[str | None, Header()] = None) -> None:
    """Guard internal endpoints with a shared secret instead of user auth, so an
    external scheduler can call them. Uses a constant-time comparison."""
    expected = settings.REMINDER_CRON_SECRET
    if not expected or not x_cron_secret or not secrets.compare_digest(x_cron_secret, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid cron secret")


def _record(
    job_runs: JobRunRepository,
    job_name: str,
    work: Callable[[], dict[str, Any]],
    status_of: Callable[[dict[str, Any]], str] = lambda _: "ok",
) -> dict[str, Any]:
    """Run one job and leave a row in ``job_runs`` saying what happened.

    Every job goes through here so the record is uniform, and so a job that raises is
    still recorded — "called and threw" and "never called" both look like nothing
    happening, and the whole point of the table is telling them apart.

    The recording never changes the caller's result: the job's own return value is
    passed straight back, and a failure re-raises so the endpoint still reports it.
    """
    run = job_runs.start(job_name)
    started_at = run.started_at
    # Tag the request scope rather than capturing here: the exception is re-raised and
    # the ASGI integration reports it once, with this tag attached. Capturing here too
    # would file the same failure twice. These endpoints are unauthenticated, so the job
    # name is the only useful grouping key.
    sentry_sdk.get_isolation_scope().set_tag("job_name", job_name)
    try:
        result = work()
    except Exception as exc:
        job_runs.fail(job_name, started_at, f"{type(exc).__name__}: {exc}")
        logger.exception("Job %s failed", job_name)
        raise
    job_runs.finish(run, status_of(result), summary=result)
    return result


@router.post("/run-reminders", dependencies=[Depends(verify_cron_secret)])
def run_reminders(
    reminder_service: Annotated[ReminderService, Depends(get_reminder_service)],
    cpi_indexing_service: Annotated[CpiIndexingService, Depends(get_cpi_indexing_service)],
    job_runs: Annotated[JobRunRepository, Depends(get_job_run_repository)],
):
    """Send overdue-rent and expiring-lease pushes. Intended to be called once a
    day by an external scheduler with the X-Cron-Secret header.

    **This job depends on `run-cpi-indexing` having run first**: indexing is what writes
    the `cpi_rent_change` confirmation rows, and this job is what pushes un-pushed rows.
    Rather than trust the scheduler's ordering — which nothing enforces — it checks for a
    successful indexing run today and performs it inline if there isn't one. Both jobs are
    idempotent (index readings upsert by month; `uq_notification_dedup` blocks duplicate
    notification rows), so a redundant re-run costs a request and nothing else.

    The inline catch-up is best-effort. If indexing fails, reminders still send: overdue
    rent and expiring leases don't depend on the index, and a CBS outage must not suppress
    unrelated pushes.
    """
    caught_up = False
    if not job_runs.succeeded_on(JOB_CPI_INDEXING, date.today()):
        try:
            _record(
                job_runs,
                JOB_CPI_INDEXING,
                cpi_indexing_service.run_cpi_indexing,
                status_of=lambda r: "stale" if r["stale"] else ("degraded" if r["degraded"] else "ok"),
            )
            caught_up = True
        except Exception:
            # Already logged and recorded as a failed run by _record.
            logger.warning("Inline CPI catch-up failed; sending reminders anyway")

    sent = _record(job_runs, JOB_REMINDERS, reminder_service.run_daily_reminders)
    return {"status": "ok", "sent": sent, "cpi_caught_up": caught_up}


@router.post("/run-cpi-indexing", dependencies=[Depends(verify_cron_secret)])
def run_cpi_indexing(
    cpi_indexing_service: Annotated[CpiIndexingService, Depends(get_cpi_indexing_service)],
    job_runs: Annotated[JobRunRepository, Depends(get_job_run_repository)],
):
    """Refresh the cached Consumer Price Index and recompute every CPI-linked renter's rent
    schedule. Called daily by an external scheduler with the X-Cron-Secret header.

    The index is read from CBS, falling back to the Bank of Israel's republication of the
    same series when CBS is unreachable. A run served by the fallback still succeeds
    (`degraded: true`) — the readings are correct, so nothing is broken.

    Returns **503** when the cache has fallen more than `CPI_MAX_STALE_MONTHS` behind the
    newest published month, which is the state that actually matters: no source is
    answering and CPI-linked rents have silently stopped tracking the index. The refresh
    itself is best-effort and never raises, so without this check a totally dead feed would
    keep returning 200 — which is exactly how a week-long CBS outage went unnoticed.
    """
    result = _record(
        job_runs,
        JOB_CPI_INDEXING,
        cpi_indexing_service.run_cpi_indexing,
        status_of=lambda r: "stale" if r["stale"] else ("degraded" if r["degraded"] else "ok"),
    )
    body = {"status": "stale" if result["stale"] else "ok", **result}
    if result["stale"]:
        # Non-2xx so the scheduler surfaces it, but with the full summary intact — the
        # DB work has already committed; only the status code differs.
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body)
    return body


@router.post("/run-retention", dependencies=[Depends(verify_cron_secret)])
def run_retention(
    retention_service: Annotated[RetentionService, Depends(get_retention_service)],
    job_runs: Annotated[JobRunRepository, Depends(get_job_run_repository)],
    dry_run: bool = False,
):
    """Age out every class of data that has a retention window: chat conversations (whose
    messages hold tenant PII), the deletion trace in activity_log, and sent-notification
    history. Each window is configured separately and `0` disables that class — the response
    lists which are disabled, so "scheduled but doing nothing" doesn't look like success.

    Call daily from an external scheduler with the X-Cron-Secret header.

    `?dry_run=true` reports what *would* be deleted and changes nothing. Worth running before
    enabling a window for the first time: the first real run removes everything already older
    than it, which for an existing database can be a lot.

    A dry run is deliberately **not** recorded in `job_runs` — it deletes nothing, so counting
    it as a run would make an unswept database look swept.
    """
    if dry_run:
        return retention_service.run(dry_run=True).as_dict()
    return _record(job_runs, JOB_RETENTION, lambda: retention_service.run().as_dict())


@router.post("/run-agent-retention", dependencies=[Depends(verify_cron_secret)])
def run_agent_retention(
    retention_service: Annotated[RetentionService, Depends(get_retention_service)],
    job_runs: Annotated[JobRunRepository, Depends(get_job_run_repository)],
):
    """Deprecated alias for `run-retention`, kept so an existing scheduler entry doesn't
    silently stop working. It now sweeps every class, not just the chat agent."""
    return _record(job_runs, JOB_RETENTION, lambda: retention_service.run().as_dict())

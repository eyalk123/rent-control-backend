import logging
import secrets
from datetime import date, datetime, timedelta
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

# The single Sentry cron monitor this backend declares. `job_runs` records what happened
# when a job runs; only a monitor with an expected schedule can report the case where
# nothing ran at all — no code executes, so nothing raises, and error reporting stays
# silent.
#
# One monitor, not one per job: the Sentry plan includes exactly one cron monitor seat
# for the whole org, so three self-declaring monitors meant whichever job checked in
# first claimed the seat and the other two were rejected over quota — and a rejected
# monitor reports nothing. So the three jobs check in nowhere; they leave their rows in
# `job_runs`, and `run-nightly-rollup` reads those rows and checks in once on their
# behalf, half an hour after the last of them.
#
# Declared in code rather than clicked into the Sentry UI so the schedule lives next to
# the job it describes; Sentry upserts the monitor from the `monitor_config` sent with
# the check-in. Keep in sync with the Railway cron entry — it runs in UTC and does not
# shift with Israeli DST, so the timezone here is UTC too.
ROLLUP_MONITOR_SLUG = "nightly-jobs"

ROLLUP_MONITOR_CONFIG: dict[str, Any] = {
    "schedule": {"type": "crontab", "value": "30 9 * * *"},
    "timezone": "UTC",
    # Minutes the rollup may be late before Sentry calls it missed.
    "checkin_margin": 15,
    # Minutes it may take before Sentry calls it timed out. Small on purpose: the rollup
    # reads three rows and sends a message — it has no real work to be slow at.
    "max_runtime": 5,
    # Alert on the first bad check-in and clear on the first good one. The jobs are
    # daily, so waiting for a second failure would mean waiting another day.
    "failure_issue_threshold": 1,
    "recovery_threshold": 1,
}

# The jobs the rollup watches, in the order they run.
ROLLUP_WATCHED_JOBS = (JOB_CPI_INDEXING, JOB_RETENTION, JOB_REMINDERS)

# How long a job may go without a successful run before the rollup calls it stale. Every
# watched job runs daily and the rollup runs at a fixed offset from all three, so "no
# success in 24h" means exactly one skipped run — the first thing worth alerting on.
STALE_AFTER = timedelta(hours=24)


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
    # `degraded` and `stale` count as having run: they describe the quality of the
    # outcome, not whether the job happened, and the rollup only asks the latter.
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


def _stale_jobs(
    job_runs: JobRunRepository, now: datetime
) -> list[tuple[str, datetime | None]]:
    """The watched jobs with no successful run inside ``STALE_AFTER``, each paired with
    its last success — ``None`` for a job that has never had one."""
    stale = []
    for job_name in ROLLUP_WATCHED_JOBS:
        last_success_at = job_runs.last_success_at(job_name)
        if last_success_at is None or now - last_success_at >= STALE_AFTER:
            stale.append((job_name, last_success_at))
    return stale


def _close_check_in(check_in_id: str | None, check_in_status: str) -> None:
    """Send the terminal check-in, best effort.

    Swallows its own failures because the rollup must not raise: a transport error while
    reporting a stale job would otherwise replace the report with a crash.
    """
    if check_in_id is None:
        return
    try:
        sentry_sdk.crons.capture_checkin(
            monitor_slug=ROLLUP_MONITOR_SLUG,
            check_in_id=check_in_id,
            status=check_in_status,
        )
    except Exception:
        logger.exception("Could not close the %s check-in", ROLLUP_MONITOR_SLUG)


@router.post("/run-nightly-rollup", dependencies=[Depends(verify_cron_secret)])
def run_nightly_rollup(
    job_runs: Annotated[JobRunRepository, Depends(get_job_run_repository)],
):
    """Check in to Sentry on behalf of all three daily jobs. Call daily from the external
    scheduler at 09:30 UTC — half an hour after `run-reminders`, the last of them.

    This is the **only** place that talks to Sentry's cron API; see the note on
    `ROLLUP_MONITOR_CONFIG` for why there is one monitor rather than three. The jobs
    themselves record their runs in `job_runs`; this reads those rows and asks one
    question of each — did it succeed at least once in the last `STALE_AFTER`? Any that
    did not are named in an error-level message, which is what says *which* job broke,
    and the check-in closes as ERROR. A job legitimately running twice in a day (the
    inline CPI catch-up in `run-reminders`) is not interesting here: once is enough.

    Never raises. A crash would send no terminal check-in at all, which Sentry reports as
    a missed or timed-out run — true, but it loses the message naming the culprit, so the
    stale path in particular is reported rather than thrown. For the same reason a stale
    result is still a 200: the alert channel is Sentry, and failing the HTTP call would
    only make the scheduler retry and check in twice.
    """
    check_in_id = None
    try:
        check_in_id = sentry_sdk.crons.capture_checkin(
            monitor_slug=ROLLUP_MONITOR_SLUG,
            status="in_progress",
            monitor_config=ROLLUP_MONITOR_CONFIG,
        )

        stale = _stale_jobs(job_runs, datetime.utcnow())
        if stale:
            stale_names = [job_name for job_name, _ in stale]
            with sentry_sdk.new_scope() as scope:
                # The timestamps go in the context, not the message: the message text is
                # the grouping key, so keeping it to the job names means one issue per
                # broken job rather than a new one every night.
                scope.set_context(
                    "stale_jobs",
                    {
                        job_name: last.isoformat() if last else "never succeeded"
                        for job_name, last in stale
                    },
                )
                sentry_sdk.capture_message(
                    "Daily jobs with no successful run in the last "
                    f"{int(STALE_AFTER.total_seconds() // 3600)}h: "
                    f"{', '.join(stale_names)}",
                    level="error",
                )
            logger.error("Stale daily jobs: %s", ", ".join(stale_names))
            _close_check_in(check_in_id, "error")
            return {"status": "error", "stale": stale_names}

        _close_check_in(check_in_id, "ok")
        return {"status": "ok", "stale": []}
    except Exception:
        # Reported as an issue like any other unhandled error, but not re-raised.
        logger.exception("Nightly rollup failed")
        sentry_sdk.capture_exception()
        _close_check_in(check_in_id, "error")
        return {"status": "error", "stale": []}

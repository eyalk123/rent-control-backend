"""The nightly rollup: one Sentry cron check-in standing in for all three daily jobs.

Sentry's plan has room for a single cron monitor, so the jobs no longer check in
themselves — they leave rows in ``job_runs`` and this endpoint reads them. What matters
is that a job which stopped running is named in an error-level message and closes the
check-in as ERROR rather than crashing, because a crash sends no message at all.
"""
from datetime import datetime, timedelta

import pytest
import sentry_sdk

from app.api.routers.internal import (
    JOB_CPI_INDEXING,
    JOB_REMINDERS,
    JOB_RETENTION,
    ROLLUP_MONITOR_CONFIG,
    ROLLUP_MONITOR_SLUG,
)
from app.config import settings
from app.models.job_run import JobRun

SECRET = "s3cret"


def _headers():
    return {"X-Cron-Secret": SECRET}


def _enable(monkeypatch):
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", SECRET)


@pytest.fixture
def sentry_calls(monkeypatch):
    """Record what the endpoint sends to Sentry. The suite runs without a DSN, so the
    real calls are silent no-ops and there is otherwise nothing to assert on."""
    calls = {"checkins": [], "messages": []}

    def capture_checkin(**kwargs):
        calls["checkins"].append(kwargs)
        return kwargs.get("check_in_id") or "check-in-id"

    def capture_message(message, level=None, **kwargs):
        calls["messages"].append((message, level))

    monkeypatch.setattr(sentry_sdk.crons, "capture_checkin", capture_checkin)
    monkeypatch.setattr(sentry_sdk, "capture_message", capture_message)
    return calls


def _seed(db_session, job_name: str, *, hours_ago: float, status: str = "ok"):
    at = datetime.utcnow() - timedelta(hours=hours_ago)
    db_session.add(
        JobRun(job_name=job_name, started_at=at, finished_at=at, status=status)
    )
    db_session.commit()


def _seed_all_fresh(db_session, *, except_for: str | None = None):
    """A successful run for each job at roughly the hour it really runs, relative to the
    09:30 rollup. ``except_for`` leaves one job with no successful run at all."""
    for job_name, hours_ago in (
        (JOB_CPI_INDEXING, 6.5),
        (JOB_RETENTION, 5.5),
        (JOB_REMINDERS, 0.5),
    ):
        if job_name != except_for:
            _seed(db_session, job_name, hours_ago=hours_ago)


def test_all_three_fresh_closes_the_check_in_ok(
    client, db_session, monkeypatch, sentry_calls
):
    _enable(monkeypatch)
    _seed_all_fresh(db_session)

    resp = client.post("/internal/run-nightly-rollup", headers=_headers())

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "stale": []}
    opened, closed = sentry_calls["checkins"]
    assert opened["monitor_slug"] == ROLLUP_MONITOR_SLUG
    assert opened["status"] == "in_progress"
    # Sent with the check-in so Sentry creates the monitor on the first one.
    assert opened["monitor_config"] == ROLLUP_MONITOR_CONFIG
    assert closed["status"] == "ok"
    assert closed["check_in_id"] == "check-in-id"
    assert sentry_calls["messages"] == []


def test_a_stale_job_is_named_and_reported_as_error(
    client, db_session, monkeypatch, sentry_calls
):
    """The whole point of the rollup: say which job stopped running."""
    _enable(monkeypatch)
    _seed_all_fresh(db_session, except_for=JOB_RETENTION)
    _seed(db_session, JOB_RETENTION, hours_ago=25)  # yesterday's run, none since

    resp = client.post("/internal/run-nightly-rollup", headers=_headers())

    assert resp.json()["stale"] == [JOB_RETENTION]
    message, level = sentry_calls["messages"][0]
    assert level == "error"
    assert JOB_RETENTION in message
    assert JOB_REMINDERS not in message and JOB_CPI_INDEXING not in message
    assert sentry_calls["checkins"][-1]["status"] == "error"


def test_a_job_that_has_never_run_is_stale(client, monkeypatch, sentry_calls):
    _enable(monkeypatch)

    resp = client.post("/internal/run-nightly-rollup", headers=_headers())

    assert resp.json()["stale"] == [JOB_CPI_INDEXING, JOB_RETENTION, JOB_REMINDERS]
    message, _ = sentry_calls["messages"][0]
    assert all(j in message for j in (JOB_CPI_INDEXING, JOB_RETENTION, JOB_REMINDERS))
    assert sentry_calls["checkins"][-1]["status"] == "error"


def test_a_failure_after_a_success_does_not_erase_it(
    client, db_session, monkeypatch, sentry_calls
):
    """The question is whether the job succeeded in the window, not how it ended last."""
    _enable(monkeypatch)
    _seed_all_fresh(db_session)
    _seed(db_session, JOB_CPI_INDEXING, hours_ago=0.1, status="failed")

    assert client.post("/internal/run-nightly-rollup", headers=_headers()).json()[
        "stale"
    ] == []


def test_a_failed_run_does_not_count_as_a_success(
    client, db_session, monkeypatch, sentry_calls
):
    """A job that has thrown every time since yesterday is exactly what this must catch —
    the rows are there, but none of them did the work."""
    _enable(monkeypatch)
    _seed_all_fresh(db_session, except_for=JOB_RETENTION)
    _seed(db_session, JOB_RETENTION, hours_ago=36)
    _seed(db_session, JOB_RETENTION, hours_ago=0.1, status="failed")

    assert client.post("/internal/run-nightly-rollup", headers=_headers()).json()[
        "stale"
    ] == [JOB_RETENTION]


def test_a_degraded_run_counts_as_a_success(
    client, db_session, monkeypatch, sentry_calls
):
    """`degraded` means the CPI refresh came from the fallback source. The readings are
    correct, so the job ran — which is the only thing the rollup asks."""
    _enable(monkeypatch)
    _seed_all_fresh(db_session, except_for=JOB_CPI_INDEXING)
    _seed(db_session, JOB_CPI_INDEXING, hours_ago=1, status="degraded")

    assert client.post("/internal/run-nightly-rollup", headers=_headers()).json()[
        "status"
    ] == "ok"


def test_the_rollup_never_raises(client, db_session, monkeypatch, sentry_calls):
    """A crash sends no terminal check-in, so Sentry reports a missed run and the
    message naming the culprit is lost. Errors here are caught and reported instead."""
    _enable(monkeypatch)
    _seed_all_fresh(db_session)

    def boom(*args, **kwargs):
        raise RuntimeError("db gone")

    monkeypatch.setattr(
        "app.repositories.job_run_repository.JobRunRepository.last_success_at", boom
    )

    resp = client.post("/internal/run-nightly-rollup", headers=_headers())

    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
    assert sentry_calls["checkins"][-1]["status"] == "error"


def test_a_sentry_transport_failure_does_not_crash_the_rollup(
    client, db_session, monkeypatch, sentry_calls
):
    _enable(monkeypatch)
    _seed_all_fresh(db_session)

    def flaky(**kwargs):
        if kwargs.get("status") == "in_progress":
            return "check-in-id"
        raise RuntimeError("sentry unreachable")

    monkeypatch.setattr(sentry_sdk.crons, "capture_checkin", flaky)
    assert client.post("/internal/run-nightly-rollup", headers=_headers()).status_code == 200


def test_the_rollup_needs_the_cron_secret(client, monkeypatch, sentry_calls):
    _enable(monkeypatch)
    assert client.post("/internal/run-nightly-rollup").status_code == 401
    assert sentry_calls["checkins"] == []


@pytest.mark.parametrize(
    "path",
    ["/internal/run-reminders", "/internal/run-cpi-indexing", "/internal/run-retention"],
)
def test_the_three_jobs_no_longer_check_in(path, client, monkeypatch, sentry_calls):
    """One monitor on the plan means one monitor in the code: a check-in from any of
    these would be rejected over quota and take the rollup's seat with it."""
    _enable(monkeypatch)
    client.post(path, headers=_headers())
    assert sentry_calls["checkins"] == []

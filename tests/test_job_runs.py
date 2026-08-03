"""job_runs: the record of whether the /internal/* jobs are actually being called.

The jobs are driven by an external scheduler the app cannot see. If it stops, retention
stops deleting and the CPI cache stops tracking the index — and until this table existed
there was nothing to tell you either had happened. Two things are being tested: that every
invocation leaves an honest row (including failures), and that the reminders job no longer
depends on the scheduler calling CPI indexing first.
"""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api.routers.internal import JOB_CPI_INDEXING, JOB_REMINDERS, JOB_RETENTION
from app.config import settings
from app.models.job_run import JobRun

SECRET = "s3cret"


def _headers():
    return {"X-Cron-Secret": SECRET}


def _enable(monkeypatch):
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", SECRET)


def _runs(db_session, job_name: str) -> list[JobRun]:
    return list(
        db_session.scalars(
            select(JobRun).where(JobRun.job_name == job_name).order_by(JobRun.id)
        )
    )


def test_a_successful_run_is_recorded_with_its_summary(client, db_session, monkeypatch):
    _enable(monkeypatch)
    resp = client.post("/internal/run-retention", headers=_headers())
    assert resp.status_code == 200

    runs = _runs(db_session, JOB_RETENTION)
    assert len(runs) == 1
    assert runs[0].status == "ok"
    assert runs[0].finished_at is not None
    # The summary is the endpoint's own body, so a run can be inspected without re-deriving it.
    assert runs[0].summary is not None


def test_a_dry_run_is_not_recorded(client, db_session, monkeypatch):
    """A dry run deletes nothing. Counting it would make an unswept database look swept."""
    _enable(monkeypatch)
    client.post("/internal/run-retention?dry_run=true", headers=_headers())
    assert _runs(db_session, JOB_RETENTION) == []


def test_a_failing_job_is_recorded_as_failed_and_still_raises(
    client, db_session, monkeypatch
):
    """'Called and threw' and 'never called' both look like nothing happening. They have
    to be distinguishable, so a failure writes a row rather than leaving none."""
    _enable(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("scheduler gremlin")

    monkeypatch.setattr("app.services.retention_service.RetentionService.run", boom)
    # Re-raised, not swallowed: recording a failure must not turn it into a silent success.
    with pytest.raises(RuntimeError, match="scheduler gremlin"):
        client.post("/internal/run-retention", headers=_headers())

    runs = _runs(db_session, JOB_RETENTION)
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert "scheduler gremlin" in runs[0].error
    assert runs[0].finished_at is not None


def test_reminders_runs_cpi_indexing_when_it_has_not_run_today(
    client, db_session, monkeypatch
):
    """The ordering dependency, removed. Reminders no longer assumes indexing ran first —
    it checks, and does the work itself when the scheduler hasn't."""
    _enable(monkeypatch)
    resp = client.post("/internal/run-reminders", headers=_headers())
    assert resp.status_code == 200
    assert resp.json()["cpi_caught_up"] is True
    assert len(_runs(db_session, JOB_CPI_INDEXING)) == 1
    assert len(_runs(db_session, JOB_REMINDERS)) == 1


def test_reminders_skips_the_catch_up_when_indexing_already_ran_today(
    client, db_session, monkeypatch
):
    """In the documented scheduler order the check is pure insurance — it must not cause
    a second indexing pass every day."""
    _enable(monkeypatch)
    db_session.add(
        JobRun(
            job_name=JOB_CPI_INDEXING,
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
            status="ok",
        )
    )
    db_session.commit()

    resp = client.post("/internal/run-reminders", headers=_headers())
    assert resp.json()["cpi_caught_up"] is False
    # Still just the row we seeded — no second pass.
    assert len(_runs(db_session, JOB_CPI_INDEXING)) == 1


def test_yesterdays_indexing_run_does_not_count_as_todays(client, db_session, monkeypatch):
    _enable(monkeypatch)
    yesterday = datetime.utcnow() - timedelta(days=1)
    db_session.add(
        JobRun(
            job_name=JOB_CPI_INDEXING,
            started_at=yesterday,
            finished_at=yesterday,
            status="ok",
        )
    )
    db_session.commit()

    resp = client.post("/internal/run-reminders", headers=_headers())
    assert resp.json()["cpi_caught_up"] is True
    assert len(_runs(db_session, JOB_CPI_INDEXING)) == 2


def test_a_failed_indexing_run_today_does_not_satisfy_the_check(
    client, db_session, monkeypatch
):
    """A run that threw did not write the cpi_rent_change rows, so it must not be treated
    as having satisfied the dependency."""
    _enable(monkeypatch)
    db_session.add(
        JobRun(
            job_name=JOB_CPI_INDEXING,
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
            status="failed",
            error="boom",
        )
    )
    db_session.commit()

    resp = client.post("/internal/run-reminders", headers=_headers())
    assert resp.json()["cpi_caught_up"] is True


def test_reminders_still_send_when_the_inline_catch_up_fails(
    client, db_session, monkeypatch
):
    """A CBS outage must not suppress overdue-rent and expiring-lease pushes, which do not
    depend on the index at all."""
    _enable(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("CBS unreachable")

    monkeypatch.setattr(
        "app.services.cpi_indexing_service.CpiIndexingService.run_cpi_indexing", boom
    )
    resp = client.post("/internal/run-reminders", headers=_headers())

    assert resp.status_code == 200
    assert resp.json()["cpi_caught_up"] is False
    # The failure is recorded, and the reminders themselves still ran.
    assert _runs(db_session, JOB_CPI_INDEXING)[0].status == "failed"
    assert len(_runs(db_session, JOB_REMINDERS)) == 1


def test_indexing_called_directly_is_recorded_once(client, db_session, monkeypatch):
    _enable(monkeypatch)
    client.post("/internal/run-cpi-indexing", headers=_headers())
    runs = _runs(db_session, JOB_CPI_INDEXING)
    assert len(runs) == 1
    assert runs[0].status in ("ok", "degraded", "stale")
    assert runs[0].started_at.date() == date.today()

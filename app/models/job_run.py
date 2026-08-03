from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from app.models.base import Base


class JobRun(Base):
    """One invocation of an ``/internal/*`` job.

    The jobs are driven by an external scheduler, so the app has no way of knowing
    whether they are still being called — a scheduler that quietly stops means
    retention stops deleting and nothing says so. This table is the record: what ran,
    when, how it ended, and what it did.

    It is written for *every* attempt, including failures, because "called and threw"
    and "never called at all" are different problems with the same symptom (nothing
    happened) and need to be told apart.

    ``status`` is deliberately coarse:

    - ``ok`` — ran to completion and did what it was asked.
    - ``degraded`` — completed, but not on the happy path (e.g. the CPI refresh was
      served by the Bank of Israel fallback rather than CBS). The result is correct;
      the route to it wasn't.
    - ``stale`` — completed, but the outcome is known-insufficient (the CPI cache has
      fallen further behind than ``CPI_MAX_STALE_MONTHS`` allows).
    - ``failed`` — raised. ``error`` holds the exception text.

    ``summary`` is the JSON body the endpoint returned, stored verbatim, so a run can
    be inspected later without re-deriving it. It holds counts and status flags only —
    no tenant data — which is why this table is not itself swept by retention. The
    table that records whether retention is running must outlive its own windows.
    """

    __tablename__ = "job_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_name = Column(String, nullable=False)  # reminders | cpi_indexing | retention
    started_at = Column(DateTime, nullable=False, default=lambda: datetime.utcnow())
    finished_at = Column(DateTime, nullable=True)  # NULL while in flight, or if the process died
    status = Column(String, nullable=False)  # ok | degraded | stale | failed
    summary = Column(Text, nullable=True)  # JSON: the endpoint's own response body
    error = Column(Text, nullable=True)  # exception text when status == "failed"

    __table_args__ = (
        # The two access patterns are "latest run of job X" and "latest *successful*
        # run of job X" — both are a descending scan of one job's rows.
        Index("ix_job_runs_name_started", "job_name", "started_at"),
    )

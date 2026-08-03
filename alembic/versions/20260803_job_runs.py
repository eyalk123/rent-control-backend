"""job_runs — a record of every /internal/* job invocation

Revision ID: 040
Revises: 039
Create Date: 2026-08-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "040"
down_revision: Union[str, None] = "039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_name", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        # Nullable: a row is written before the job runs, so a process killed mid-run
        # leaves a visible unfinished row rather than no row at all.
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_runs_name_started", "job_runs", ["job_name", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_job_runs_name_started", table_name="job_runs")
    op.drop_table("job_runs")

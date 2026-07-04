"""cpi_index cache table and renters.cpi_base_index for CPI rent linkage

Revision ID: 032
Revises: 031
Create Date: 2026-07-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cpi_index",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("index_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "index_id", "year", "month", name="uq_cpi_index_series_period"
        ),
    )
    # Table is created empty; POST /internal/run-cpi-indexing backfills history from
    # the CBS API on its first run.

    op.add_column("renters", sa.Column("cpi_base_index", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("renters", "cpi_base_index")
    op.drop_table("cpi_index")

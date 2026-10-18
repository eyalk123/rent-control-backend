"""cpi_base_factors: CBS chaining coefficients, so readings on two index bases compare

CBS restarts the CPI near 100 at each base change (January 2021, 2023, 2025...). Readings
arriving on another base are converted to the series' stored scale before they are cached;
this table keeps the official coefficient used for each base. No existing row changes:
everything cached in production is already on the "2024 average" scale.

Revision ID: 076
Revises: 075
Create Date: 2026-10-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "076"
down_revision: Union[str, None] = "075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cpi_base_factors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("index_id", sa.Integer(), nullable=False),
        sa.Column("base", sa.String(), nullable=False),
        sa.Column("factor", sa.Float(), nullable=False),
        sa.Column("coefficient", sa.Float(), nullable=False),
        sa.Column("from_month", sa.String(), nullable=False),
        sa.Column("to_month", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("index_id", "base", name="uq_cpi_base_factors_index_base"),
    )


def downgrade() -> None:
    op.drop_table("cpi_base_factors")

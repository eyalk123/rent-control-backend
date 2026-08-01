"""cpi_index.source — provenance of each cached CPI reading

Revision ID: 038
Revises: 037
Create Date: 2026-08-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "038"
down_revision: Union[str, None] = "037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default="cbs" backfills any existing row: historically the CBS API was the
    # only writer. Keeping the column NOT NULL means no code path has to handle a NULL
    # source when deciding precedence.
    op.add_column(
        "cpi_index",
        sa.Column("source", sa.String(), nullable=False, server_default="cbs"),
    )


def downgrade() -> None:
    op.drop_column("cpi_index", "source")

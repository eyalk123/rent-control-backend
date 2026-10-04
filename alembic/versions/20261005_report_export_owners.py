"""report_export_owners — record which property owners a report was limited to

A report can now cover only some of the portfolio's property owners, and be downloaded as a
ZIP with one file per owner. The history row records both, so re-exporting it reproduces
the same report rather than the whole portfolio. Every existing row covered every owner in
one file, which is what NULL and the server default record for them.

Revision ID: 068
Revises: 067
Create Date: 2026-10-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "068"
down_revision: Union[str, None] = "067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("report_exports", sa.Column("owners", sa.JSON(), nullable=True))
    op.add_column(
        "report_exports",
        sa.Column("split_by_owner", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("report_exports", "split_by_owner")
    op.drop_column("report_exports", "owners")

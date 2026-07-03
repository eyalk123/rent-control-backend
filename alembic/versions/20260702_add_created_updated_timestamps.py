"""add created_at/updated_at to properties, renters, suppliers, expense_categories

Adds entity-creation timestamps so growth/cohort analytics can chart when
properties, renters, suppliers, and categories were added. Existing rows are
backfilled with the migration time (now()) since their true creation date is
unknown — treat pre-migration timestamps as "on or before this deploy".

Revision ID: 031
Revises: 030
Create Date: 2026-07-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = ("properties", "renters", "suppliers", "expense_categories")


def upgrade() -> None:
    for table in _TABLES:
        # Add NOT NULL with a server_default so existing rows backfill to now();
        # then drop the server_default so the app (Python default=datetime.utcnow)
        # owns the value for new rows, matching the owners table convention.
        op.add_column(
            table,
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.alter_column(table, "created_at", server_default=None)
        op.alter_column(table, "updated_at", server_default=None)


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "updated_at")
        op.drop_column(table, "created_at")

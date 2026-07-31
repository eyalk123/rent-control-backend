"""Append-only activity log, recording deletions

Revision ID: 036
Revises: 035
Create Date: 2026-07-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "036"
down_revision: Union[str, None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activity_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_activity_log_owner_id", "activity_log", ["owner_id"])
    op.create_index("ix_activity_log_created_at", "activity_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_activity_log_created_at", table_name="activity_log")
    op.drop_index("ix_activity_log_owner_id", table_name="activity_log")
    op.drop_table("activity_log")

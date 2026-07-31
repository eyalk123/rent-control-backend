"""Anonymous tombstone for deleted accounts

Revision ID: 037
Revises: 036
Create Date: 2026-07-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "037"
down_revision: Union[str, None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deleted_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id_hash", sa.String(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=False),
        sa.Column("properties_count", sa.Integer(), nullable=False),
        sa.Column("renters_count", sa.Integer(), nullable=False),
        sa.Column("transactions_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deleted_accounts_owner_id_hash", "deleted_accounts", ["owner_id_hash"])
    op.create_index("ix_deleted_accounts_deleted_at", "deleted_accounts", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_deleted_accounts_deleted_at", table_name="deleted_accounts")
    op.drop_index("ix_deleted_accounts_owner_id_hash", table_name="deleted_accounts")
    op.drop_table("deleted_accounts")

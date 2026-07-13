"""Add 'check' value to paymentmethodenum

Revision ID: 034
Revises: 033
Create Date: 2026-07-12

"""
from typing import Sequence, Union

from alembic import op

revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres cannot run ALTER TYPE ... ADD VALUE inside the transaction Alembic opens,
    # so execute it in an autocommit block. See paymentmethodenum creation in
    # 20250314_transactions_tables.py.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE paymentmethodenum ADD VALUE IF NOT EXISTS 'check'")


def downgrade() -> None:
    # Postgres has no direct "DROP VALUE" for an enum; removing it would require recreating
    # the type and rewriting the column. Not worth the risk for a downgrade path, so no-op.
    pass

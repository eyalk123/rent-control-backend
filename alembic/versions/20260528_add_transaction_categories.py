"""add transaction_categories junction table

Revision ID: 021
Revises: 020
Create Date: 2026-05-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transaction_categories",
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("transactions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("expense_categories.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_transaction_categories_transaction_id", "transaction_categories", ["transaction_id"])
    op.create_index("ix_transaction_categories_category_id", "transaction_categories", ["category_id"])
    # Backfill from the existing single-FK column
    op.execute(
        "INSERT INTO transaction_categories (transaction_id, category_id) "
        "SELECT id, category_id FROM transactions WHERE category_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_transaction_categories_category_id", table_name="transaction_categories")
    op.drop_index("ix_transaction_categories_transaction_id", table_name="transaction_categories")
    op.drop_table("transaction_categories")

"""Add owner_id to transactions for ownership queries independent of property FK

Revision ID: 014
Revises: 013
Create Date: 2026-05-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("owner_id", sa.String(), nullable=True))
    op.execute("""
        UPDATE transactions t
        SET owner_id = p.owner_id
        FROM properties p
        WHERE t.property_id = p.id
    """)
    op.alter_column("transactions", "owner_id", nullable=False)


def downgrade() -> None:
    op.drop_column("transactions", "owner_id")

"""Replace monthly_rent with lease_years on renters

Revision ID: 004
Revises: 003
Create Date: 2025-03-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("renters", sa.Column("lease_years", sa.Text(), nullable=True))

    # Migrate existing data: convert monthly_rent to a single-year contract entry
    op.execute(
        """
        UPDATE renters
        SET lease_years = '[{"amount": ' || (monthly_rent * 12)::text || ', "type": "contract"}]'
        """
    )

    op.alter_column("renters", "lease_years", nullable=False)
    op.drop_column("renters", "monthly_rent")


def downgrade() -> None:
    op.add_column("renters", sa.Column("monthly_rent", sa.Float(), nullable=True))

    # Restore monthly_rent from first lease year entry
    op.execute(
        """
        UPDATE renters
        SET monthly_rent = (lease_years::json->0->>'amount')::float / 12
        """
    )

    op.alter_column("renters", "monthly_rent", nullable=False)
    op.drop_column("renters", "lease_years")

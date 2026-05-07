"""Add owner_id to renters for ownership queries independent of property FK

Revision ID: 015
Revises: 014
Create Date: 2026-05-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("renters", sa.Column("owner_id", sa.String(), nullable=True))
    op.execute("""
        UPDATE renters r
        SET owner_id = p.owner_id
        FROM properties p
        WHERE r.property_id = p.id
    """)


def downgrade() -> None:
    op.drop_column("renters", "owner_id")

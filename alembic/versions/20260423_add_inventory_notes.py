"""Add inventory_notes to properties

Revision ID: 008
Revises: 007
Create Date: 2026-04-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("inventory_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("properties", "inventory_notes")

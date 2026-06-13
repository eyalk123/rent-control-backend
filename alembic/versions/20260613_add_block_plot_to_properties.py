"""Add block and plot to properties

Revision ID: 024
Revises: 023
Create Date: 2026-06-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("block", sa.String(), nullable=True))
    op.add_column("properties", sa.Column("plot", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("properties", "plot")
    op.drop_column("properties", "block")

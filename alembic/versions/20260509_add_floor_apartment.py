"""Add floor and apartment to properties

Revision ID: 016
Revises: cacb73f7cef5
Create Date: 2026-05-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "016"
down_revision: Union[str, None] = "cacb73f7cef5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("floor", sa.Integer(), nullable=True))
    op.add_column("properties", sa.Column("apartment", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("properties", "apartment")
    op.drop_column("properties", "floor")

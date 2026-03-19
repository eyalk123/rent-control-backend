"""Add contact_id to renters

Revision ID: 005
Revises: 004
Create Date: 2025-03-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("renters", sa.Column("contact_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("renters", "contact_id")

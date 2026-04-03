"""Add extra_contacts JSON column to renters table

Revision ID: 009
Revises: 008
Create Date: 2026-04-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("renters", sa.Column("extra_contacts", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("renters", "extra_contacts")

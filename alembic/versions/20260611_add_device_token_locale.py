"""add locale column to device_tokens

Revision ID: 023
Revises: 022
Create Date: 2026-06-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("device_tokens", sa.Column("locale", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("device_tokens", "locale")

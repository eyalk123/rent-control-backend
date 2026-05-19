"""add receipt_image_url to transactions

Revision ID: 020
Revises: 019
Create Date: 2026-05-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("receipt_image_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "receipt_image_url")

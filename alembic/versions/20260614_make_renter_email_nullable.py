"""Make renter email nullable

Revision ID: 025
Revises: 024
Create Date: 2026-06-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("renters", "email", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    op.alter_column("renters", "email", existing_type=sa.String(), nullable=False)

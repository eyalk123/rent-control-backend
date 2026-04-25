"""Add basic_contract_url to properties and full_contract_url to renters

Revision ID: 011
Revises: 010
Create Date: 2026-04-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("basic_contract_url", sa.String(), nullable=True))
    op.add_column("renters", sa.Column("full_contract_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("properties", "basic_contract_url")
    op.drop_column("renters", "full_contract_url")

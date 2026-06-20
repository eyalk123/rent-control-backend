"""Add structured lease-term intent fields to renters

Revision ID: 027
Revises: 026
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("renters", sa.Column("contract_term_years", sa.Integer(), nullable=True))
    op.add_column("renters", sa.Column("option_years", sa.Integer(), nullable=True))
    op.add_column("renters", sa.Column("base_rent", sa.Float(), nullable=True))
    op.add_column("renters", sa.Column("rent_escalation_mode", sa.String(), nullable=True))
    op.add_column("renters", sa.Column("rent_escalation_value", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("renters", "rent_escalation_value")
    op.drop_column("renters", "rent_escalation_mode")
    op.drop_column("renters", "base_rent")
    op.drop_column("renters", "option_years")
    op.drop_column("renters", "contract_term_years")

"""tax_year_inputs: other income and credit points per tax group and year

The marginal track of the tax report needs both. They belong to a household's tax year,
not to a property or an owner. See ``app/models/tax_year_input.py``.

Revision ID: 080
Revises: 079
Create Date: 2026-10-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "080"
down_revision: Union[str, None] = "079"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tax_year_inputs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("group_key", sa.String(40), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("other_income", sa.Float(), nullable=True),
        sa.Column("credit_points", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("owner_id", "group_key", "year", name="uq_tax_year_inputs_group_year"),
    )
    op.create_index("ix_tax_year_inputs_owner_id", "tax_year_inputs", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_tax_year_inputs_owner_id", table_name="tax_year_inputs")
    op.drop_table("tax_year_inputs")

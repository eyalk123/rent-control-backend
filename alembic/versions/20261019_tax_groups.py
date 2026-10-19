"""tax_groups: households the tax tracks report tests together

Owners of one household share one exemption ceiling. Each row merges owners, or picks
properties, into one group for the tax report only. See ``app/models/tax_group.py``.

Revision ID: 079
Revises: 078
Create Date: 2026-10-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "079"
down_revision: Union[str, None] = "078"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tax_groups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("property_owner_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("property_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tax_groups_owner_id", "tax_groups", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_tax_groups_owner_id", table_name="tax_groups")
    op.drop_table("tax_groups")

"""tax_people: the tax report's answers per person, not per saved group

The tax tracks report now works on whatever owners and flats the account holder picks on the
page, so saved household groups go: ``tax_groups`` is dropped, and the other income and credit
points in ``tax_year_inputs`` belong to one person (``owner:<id>``, or ``none`` for properties
with no owner). Rows kept for a saved group (``custom:<id>``) have no person to belong to and
are deleted. The column is renamed ``person_key`` to say so.

Revision ID: 083
Revises: 082
Create Date: 2026-10-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "083"
down_revision: Union[str, None] = "082"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM tax_year_inputs WHERE group_key LIKE 'custom:%'")
    op.drop_constraint("uq_tax_year_inputs_group_year", "tax_year_inputs", type_="unique")
    op.alter_column("tax_year_inputs", "group_key", new_column_name="person_key")
    op.create_unique_constraint(
        "uq_tax_year_inputs_person_year", "tax_year_inputs", ["owner_id", "person_key", "year"]
    )
    op.drop_index("ix_tax_groups_owner_id", table_name="tax_groups")
    op.drop_table("tax_groups")


def downgrade() -> None:
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
    op.drop_constraint("uq_tax_year_inputs_person_year", "tax_year_inputs", type_="unique")
    op.alter_column("tax_year_inputs", "person_key", new_column_name="group_key")
    op.create_unique_constraint(
        "uq_tax_year_inputs_group_year", "tax_year_inputs", ["owner_id", "group_key", "year"]
    )

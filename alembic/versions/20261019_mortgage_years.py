"""mortgage_years: the bank's yearly mortgage summary (mortgage plan, layer 2)

Interest, linkage differences and the balance on December 31, per mortgage and year, with
an optional link to the statement kept as a property file. See ``MortgageYear``.

Revision ID: 081
Revises: 080
Create Date: 2026-10-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "081"
down_revision: Union[str, None] = "080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mortgage_years",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "mortgage_id",
            sa.Integer(),
            sa.ForeignKey("property_mortgages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("interest", sa.Float(), nullable=False),
        sa.Column("linkage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("balance", sa.Float(), nullable=True),
        sa.Column(
            "property_file_id",
            sa.Integer(),
            sa.ForeignKey("property_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("mortgage_id", "year", name="uq_mortgage_years_year"),
    )
    op.create_index("ix_mortgage_years_mortgage_id", "mortgage_years", ["mortgage_id"])


def downgrade() -> None:
    op.drop_index("ix_mortgage_years_mortgage_id", table_name="mortgage_years")
    op.drop_table("mortgage_years")

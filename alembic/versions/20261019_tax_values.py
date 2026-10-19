"""tax_documents + tax_values: government figures for the tax tracks report

The Tax Authority's yearly booklet (one row per country and tax year, with its fingerprint
so a republished file is noticed) and the figures read from it or from the law, per year.
Filled by the daily refresh; nothing reads them yet.

Revision ID: 077
Revises: 076
Create Date: 2026-10-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "077"
down_revision: Union[str, None] = "076"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tax_documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("tax_year", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("country", "tax_year", name="uq_tax_documents_country_year"),
    )
    op.create_table(
        "tax_values",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("tax_year", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("reference", sa.Text(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "country", "tax_year", "key", name="uq_tax_values_country_year_key"
        ),
    )


def downgrade() -> None:
    op.drop_table("tax_values")
    op.drop_table("tax_documents")

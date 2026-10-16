"""property_mortgages — the monthly mortgage payment per property, as a dated history

Layer 1 of the mortgage plan (`docs/תוכנית משכנתא ב-RentVance.md`): what goes to the bank
each month, the day it goes, and optionally when the loan ends. Not the loan's structure —
see `PropertyMortgage`.

Two tables: a mortgage per property, and its payment amounts from a date on, so a payment
changed after a rate change leaves the earlier months as they were.

Revision ID: 073
Revises: 072
Create Date: 2026-10-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "073"
down_revision: Union[str, None] = "072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "property_mortgages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "property_id",
            sa.Integer(),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("charge_day", sa.Integer(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_property_mortgages_property_id", "property_mortgages", ["property_id"])
    op.create_table(
        "mortgage_payments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "mortgage_id",
            sa.Integer(),
            sa.ForeignKey("property_mortgages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_mortgage_payments_mortgage_id", "mortgage_payments", ["mortgage_id"])


def downgrade() -> None:
    op.drop_index("ix_mortgage_payments_mortgage_id", table_name="mortgage_payments")
    op.drop_table("mortgage_payments")
    op.drop_index("ix_property_mortgages_property_id", table_name="property_mortgages")
    op.drop_table("property_mortgages")

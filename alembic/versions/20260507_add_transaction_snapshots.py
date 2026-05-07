"""Add property_address and renter_name snapshot columns to transactions; make property_id nullable with SET NULL

Revision ID: 013
Revises: 012
Create Date: 2026-05-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("property_address", sa.Text(), nullable=True))
    op.add_column("transactions", sa.Column("renter_name", sa.Text(), nullable=True))

    op.drop_constraint("transactions_property_id_fkey", "transactions", type_="foreignkey")
    op.alter_column("transactions", "property_id", nullable=True)
    op.create_foreign_key(
        None, "transactions", "properties", ["property_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint(None, "transactions", type_="foreignkey")
    op.alter_column("transactions", "property_id", nullable=False)
    op.create_foreign_key(
        "transactions_property_id_fkey",
        "transactions",
        "properties",
        ["property_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("transactions", "property_address")
    op.drop_column("transactions", "renter_name")

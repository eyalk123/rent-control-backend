"""Rename water_meter_tax to water_meter_number, add electricity/water account numbers

Revision ID: 018
Revises: 017
Create Date: 2026-05-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "properties",
        "water_meter_tax",
        new_column_name="water_meter_number",
        type_=sa.String(),
        existing_type=sa.Float(),
        existing_nullable=True,
        postgresql_using="water_meter_tax::text",
    )
    op.add_column("properties", sa.Column("electricity_account_number", sa.String(), nullable=True))
    op.add_column("properties", sa.Column("water_account_number", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("properties", "water_account_number")
    op.drop_column("properties", "electricity_account_number")
    op.alter_column(
        "properties",
        "water_meter_number",
        new_column_name="water_meter_tax",
        type_=sa.Float(),
        existing_type=sa.String(),
        existing_nullable=True,
        postgresql_using="water_meter_number::double precision",
    )

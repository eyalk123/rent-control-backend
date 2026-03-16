"""Property and renter new fields

Revision ID: 002
Revises: 001
Create Date: 2025-03-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("number_of_rooms", sa.Integer(), nullable=True))
    op.add_column("properties", sa.Column("parking_numbers", sa.Text(), nullable=True))
    op.add_column("properties", sa.Column("electricity_meter_number", sa.String(), nullable=True))
    op.add_column("properties", sa.Column("water_meter_tax", sa.Float(), nullable=True))
    op.add_column("properties", sa.Column("property_tax", sa.Float(), nullable=True))
    op.add_column("properties", sa.Column("house_committee", sa.Float(), nullable=True))

    op.add_column("renters", sa.Column("number_of_payments", sa.Integer(), nullable=True))
    op.add_column("renters", sa.Column("payment_type", sa.String(), nullable=True))
    op.add_column("renters", sa.Column("payment_day_of_month", sa.Integer(), nullable=True))
    op.add_column("renters", sa.Column("insurance_type", sa.String(), nullable=True))
    op.add_column("renters", sa.Column("insurance_amount", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("renters", "insurance_amount")
    op.drop_column("renters", "insurance_type")
    op.drop_column("renters", "payment_day_of_month")
    op.drop_column("renters", "payment_type")
    op.drop_column("renters", "number_of_payments")

    op.drop_column("properties", "house_committee")
    op.drop_column("properties", "property_tax")
    op.drop_column("properties", "water_meter_tax")
    op.drop_column("properties", "electricity_meter_number")
    op.drop_column("properties", "parking_numbers")
    op.drop_column("properties", "number_of_rooms")

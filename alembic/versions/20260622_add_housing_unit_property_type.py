"""Add housing_unit (and backfill garden_apartment) to property type enum

Revision ID: 028
Revises: 027
Create Date: 2026-06-22

"""
from typing import Sequence, Union

from alembic import op


revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `garden_apartment` was added to the model/schema enums earlier but never
    # migrated into the Postgres `propertytypeenum` type; add it here too so the
    # DB type matches the application enum. IF NOT EXISTS keeps this idempotent.
    op.execute("ALTER TYPE propertytypeenum ADD VALUE IF NOT EXISTS 'garden_apartment'")
    op.execute("ALTER TYPE propertytypeenum ADD VALUE IF NOT EXISTS 'housing_unit'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type without
    # recreating it; leave the added values in place on downgrade.
    pass

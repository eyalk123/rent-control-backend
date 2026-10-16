"""property_mortgages.has_prime_track — whether part of the loan is on the prime track

Asked in the property form so the owner can be told when the Bank of Israel changes the
rate, which moves the prime part of the payment. Existing mortgages start on, like new ones:
an owner who isn't sure is better told once too often than missed.

Revision ID: 074
Revises: 073
Create Date: 2026-10-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "074"
down_revision: Union[str, None] = "073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "property_mortgages",
        sa.Column("has_prime_track", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("property_mortgages", "has_prime_track")

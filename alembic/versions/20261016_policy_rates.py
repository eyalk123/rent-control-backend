"""policy_rates + the mortgage_rate_change notification type

The Bank of Israel rate, one row per change, and the alert sent when it moves to owners
whose mortgage has a prime track (`property_mortgages.has_prime_track`).

Revision ID: 075
Revises: 074
Create Date: 2026-10-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "075"
down_revision: Union[str, None] = "074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_VALUE = "mortgage_rate_change"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # ALTER TYPE ... ADD VALUE cannot run inside the transaction Alembic opens.
        # Same idiom as 20260802_cpi_rent_change_notification.py.
        with op.get_context().autocommit_block():
            op.execute(
                f"ALTER TYPE notificationtypeenum ADD VALUE IF NOT EXISTS '{_NEW_VALUE}'"
            )
    # SQLite (tests) stores the enum as a plain string — nothing to alter.

    op.create_table(
        "policy_rates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("rate", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("country", "effective_date", name="uq_policy_rates_country_date"),
    )


def downgrade() -> None:
    op.drop_table("policy_rates")
    # The enum value is deliberately left in place: PostgreSQL cannot drop one.

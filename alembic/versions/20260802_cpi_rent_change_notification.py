"""cpi_rent_change notification type + its materiality threshold

Revision ID: 039
Revises: 038
Create Date: 2026-08-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "039"
down_revision: Union[str, None] = "038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_VALUE = "cpi_rent_change"


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        # ALTER TYPE ... ADD VALUE cannot run inside a transaction block, and Alembic
        # wraps the migration in one. AUTOCOMMIT for this statement only.
        with bind.execution_options(isolation_level="AUTOCOMMIT").begin() as conn:
            conn.exec_driver_sql(
                f"ALTER TYPE notificationtypeenum ADD VALUE IF NOT EXISTS '{_NEW_VALUE}'"
            )
    # SQLite (tests) stores the enum as a plain string — nothing to alter.

    # Materiality floor for CPI change alerts. NOT NULL with a server default so every
    # existing settings row gets the product default without a backfill pass.
    op.add_column(
        "notification_settings",
        sa.Column(
            "cpi_min_change_amount", sa.Float(), nullable=False, server_default="10"
        ),
    )
    op.add_column(
        "notification_settings",
        sa.Column(
            "cpi_min_change_percent", sa.Float(), nullable=False, server_default="0.5"
        ),
    )


def downgrade() -> None:
    op.drop_column("notification_settings", "cpi_min_change_percent")
    op.drop_column("notification_settings", "cpi_min_change_amount")
    # The enum value is deliberately left in place: PostgreSQL cannot drop one, and
    # recreating the type would need every notifications row rewritten.

"""notification rules engine: settings + rules tables, feed columns

Revision ID: 026
Revises: 025
Create Date: 2026-06-16

Adds per-user notification_settings and user-composed notification_rules, and
upgrades notification_log into the notifications feed (offset, data, push/read
state) with an offset-aware dedup constraint.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Reuse the enum created in migration 022 rather than redefining it.
notification_type_enum = ENUM(
    "overdue", "lease_expiring", name="notificationtypeenum", create_type=False
)


def upgrade() -> None:
    op.create_table(
        "notification_settings",
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("master_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("muted_events", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("owner_id"),
    )

    op.create_table(
        "notification_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("event_type", notification_type_enum, nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("offsets", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("scope_property_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("scope_property_owners", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("scope_renter_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notification_rules_owner_id", "notification_rules", ["owner_id"], unique=False
    )

    # notification_log -> notifications feed.
    op.rename_table("notification_log", "notifications")
    op.add_column(
        "notifications",
        sa.Column("offset", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("notifications", sa.Column("data", sa.Text(), nullable=True))
    op.add_column("notifications", sa.Column("pushed_at", sa.DateTime(), nullable=True))
    op.add_column("notifications", sa.Column("read_at", sa.DateTime(), nullable=True))
    op.add_column("notifications", sa.Column("dismissed_at", sa.DateTime(), nullable=True))
    op.drop_constraint("uq_notification_log_dedup", "notifications", type_="unique")
    op.create_unique_constraint(
        "uq_notification_dedup",
        "notifications",
        ["owner_id", "type", "entity_id", "period_key", "offset"],
    )
    op.create_index("ix_notifications_owner_id", "notifications", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_notifications_owner_id", table_name="notifications")
    op.drop_constraint("uq_notification_dedup", "notifications", type_="unique")
    op.create_unique_constraint(
        "uq_notification_log_dedup",
        "notifications",
        ["owner_id", "type", "entity_id", "period_key"],
    )
    op.drop_column("notifications", "dismissed_at")
    op.drop_column("notifications", "read_at")
    op.drop_column("notifications", "pushed_at")
    op.drop_column("notifications", "data")
    op.drop_column("notifications", "offset")
    op.rename_table("notifications", "notification_log")

    op.drop_index("ix_notification_rules_owner_id", table_name="notification_rules")
    op.drop_table("notification_rules")
    op.drop_table("notification_settings")

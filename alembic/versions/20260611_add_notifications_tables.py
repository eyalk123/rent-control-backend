"""device_tokens and notification_log tables for push notifications

Revision ID: 022
Revises: 021
Create Date: 2026-06-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN CREATE TYPE deviceplatformenum AS ENUM ('ios', 'android', 'web'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    op.execute(
        "DO $$ BEGIN CREATE TYPE notificationtypeenum AS ENUM ('overdue', 'lease_expiring'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )

    device_platform_enum = ENUM(
        "ios", "android", "web", name="deviceplatformenum", create_type=False
    )
    notification_type_enum = ENUM(
        "overdue", "lease_expiring", name="notificationtypeenum", create_type=False
    )

    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("platform", device_platform_enum, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_used_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_device_tokens_token"),
    )
    op.create_index("ix_device_tokens_owner_id", "device_tokens", ["owner_id"], unique=False)

    op.create_table(
        "notification_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("type", notification_type_enum, nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("period_key", sa.String(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "type", "entity_id", "period_key",
            name="uq_notification_log_dedup",
        ),
    )


def downgrade() -> None:
    op.drop_table("notification_log")
    op.drop_index("ix_device_tokens_owner_id", table_name="device_tokens")
    op.drop_table("device_tokens")
    op.execute("DROP TYPE notificationtypeenum")
    op.execute("DROP TYPE deviceplatformenum")

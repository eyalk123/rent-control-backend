"""subscription_events — webhook idempotency and audit; subscriptions.last_event_at

Revision ID: 062
Revises: 061
Create Date: 2026-09-21

Webhook delivery guarantees neither exactly-once nor in-order, and this migration adds the
two pieces of state that make both survivable.

`subscription_events.event_id` is UNIQUE. RevenueCat retries a delivery up to five times
and a retry carries the same id, so without this a retry following a slow-but-successful
response re-applies the event. The unique index makes the second *insert* fail rather than
the second *application* succeed.

`subscriptions.last_event_at` holds the `event_timestamp_ms` of the last webhook applied.
Events can arrive out of order — a RENEWAL after the CANCELLATION that supersedes it — and
comparing against this is what lets a stale event be recorded and discarded instead of
moving an account backwards into a status that has already been replaced.

The table also keeps the raw payload. Billing disputes are answered with evidence, and
"why did this account lose access on the 14th" should not depend on reconstructing history
from the current row. It holds no renter data: RevenueCat payloads carry product ids,
prices and store identifiers, not tenants.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "062"
down_revision: Union[str, None] = "061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subscription_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=True),
        sa.Column("store", sa.String(), nullable=True),
        sa.Column("environment", sa.String(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("applied", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique — this is the idempotency guard, not merely a lookup index.
    op.create_index(
        op.f("ix_subscription_events_event_id"),
        "subscription_events",
        ["event_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_subscription_events_owner_id"), "subscription_events", ["owner_id"]
    )
    op.create_index(
        "ix_subscription_events_owner_occurred",
        "subscription_events",
        ["owner_id", "occurred_at"],
    )

    op.add_column(
        "subscriptions", sa.Column("last_event_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "last_event_at")
    op.drop_index(
        "ix_subscription_events_owner_occurred", table_name="subscription_events"
    )
    op.drop_index(
        op.f("ix_subscription_events_owner_id"), table_name="subscription_events"
    )
    op.drop_index(
        op.f("ix_subscription_events_event_id"), table_name="subscription_events"
    )
    op.drop_table("subscription_events")

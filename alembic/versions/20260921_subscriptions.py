"""subscriptions — the local record of billing state, and the free-tier scan quota index

Revision ID: 060
Revises: 059
Create Date: 2026-09-21

This table is what the product gates on. The payment providers (Apple and Google via
RevenueCat, Paddle on the web) are ingestion only: their webhooks upsert a row here, and
every entitlement check reads from here. That indirection is deliberate — a provider
outage or a later migration off RevenueCat must not change what a paying landlord can do.

`owner_id` is UNIQUE rather than merely indexed. A landlord has one subscription, and the
constraint is what makes "subscribed on iOS and on the web at the same time" impossible to
represent instead of merely discouraged. Double billing through two rails that know
nothing about each other is the expensive bug in this design, and the database is the
cheapest place to make it fail.

No foreign key to `owners`. Every other table follows the same rule: `owner_id` is a
Firebase UID and Firebase is the source of truth for whether it exists.

The second index is unrelated to billing state and is here because the free plan's
three-lease-scans-a-month limit is counted directly off `document_extraction_logs` rather
than kept in a counter. That count runs on every scan attempt and filters owner plus a
date range; `ix_document_extraction_logs_owner_id` alone leaves it scanning an owner's
whole history, which grows for exactly the accounts that use the feature most.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "060"
down_revision: Union[str, None] = "059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("plan", sa.String(), nullable=False),
        sa.Column("period", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("current_period_end", sa.DateTime(), nullable=True),
        sa.Column("grace_until", sa.DateTime(), nullable=True),
        sa.Column("price_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("price_currency", sa.String(length=3), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique, not just indexed — see the docstring. This is the double-billing guard.
    op.create_index(
        op.f("ix_subscriptions_owner_id"), "subscriptions", ["owner_id"], unique=True
    )
    op.create_index(
        op.f("ix_subscriptions_external_id"), "subscriptions", ["external_id"]
    )

    # Serves the free-plan scan quota count (owner + month window).
    op.create_index(
        "ix_document_extraction_logs_owner_created",
        "document_extraction_logs",
        ["owner_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_extraction_logs_owner_created",
        table_name="document_extraction_logs",
    )
    op.drop_index(op.f("ix_subscriptions_external_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_owner_id"), table_name="subscriptions")
    op.drop_table("subscriptions")

"""owner_client_days — which client each owner actually works in, per day

Two clients, one backend, and nothing that said which one an owner used: both sent only
Content-Type and Authorization, so every request looked identical. Signup platform was
already known (legal_acceptances.platform) and push registration was already known
(device_tokens.platform); day-to-day usage was not.

One row per (owner, day, app, platform), carrying a request count and a write count. See
app/models/owner_client_day.py for why it is shaped that way rather than as a nullable
column on every record table, and app/services/client_usage_service.py for what counts as
a write.

The unique constraint is the point of the table, not decoration: the flush is an
ON CONFLICT ... DO UPDATE that *adds* the accumulated counts, so several workers each
flushing their own in-memory tally sum into one row instead of overwriting each other.

Revision ID: 055
Revises: 054
Create Date: 2026-09-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "055"
down_revision: Union[str, None] = "054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "owner_client_days",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        # Plain strings, not a PG enum: an enum value cannot be dropped without rewriting
        # the type and every column using it, and this vocabulary changes as clients do.
        # "unknown" rather than NULL for a missing header, so counting never special-cases
        # it — clients that predate the headers are a permanent case.
        sa.Column("app", sa.String(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("app_version", sa.String(), nullable=True),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("writes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "day", "app", "platform", name="uq_owner_client_days_key"
        ),
    )
    op.create_index("ix_owner_client_days_day", "owner_client_days", ["day"])


def downgrade() -> None:
    op.drop_index("ix_owner_client_days_day", table_name="owner_client_days")
    op.drop_table("owner_client_days")

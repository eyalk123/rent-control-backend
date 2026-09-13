"""country_notify_requests — "tell me when you add {Country}"

Optional, non-blocking, and owner-scoped. No country is refused an account, so this is a
preference expressed from inside the product rather than a waiting list outside it — which
is why it hangs off an owner_id rather than capturing an anonymous email, and why account
deletion sweeps it like every other owner-scoped table.

Unique on (owner_id, country_code): asking twice is the same fact, not a stronger one, and
double-counting would skew the only signal this table exists to give.

Revision ID: 050
Revises: 049
Create Date: 2026-09-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "050"
down_revision: Union[str, None] = "049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "country_notify_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "country_code", name="uq_country_notify_owner_country"
        ),
    )
    op.create_index(
        "ix_country_notify_requests_owner_id",
        "country_notify_requests",
        ["owner_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_country_notify_requests_owner_id", table_name="country_notify_requests")
    op.drop_table("country_notify_requests")

"""Drop country_notify_requests — the notify-me capture is gone.

It was collected on a post-signup screen that told a non-Israeli user what the product
does *not* do for their country yet, and offered to tell them when it did. That screen
stood between someone and their portfolio on first contact to deliver an absence, so it
was removed; the capture had no other entry point and would have become a table nobody
writes to.

The signal it gave — which market earns a native pack next — now has to come from signup
counts per country, which `owners.country` already carries. A coarser number, but one
nobody has to be shown a disclaimer to produce.

Irreversible in practice: `downgrade` rebuilds the table, not the rows.

Revision ID: 056
Revises: 055
Create Date: 2026-09-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "056"
down_revision: Union[str, None] = "055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(
        "ix_country_notify_requests_owner_id", table_name="country_notify_requests"
    )
    op.drop_table("country_notify_requests")


def downgrade() -> None:
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

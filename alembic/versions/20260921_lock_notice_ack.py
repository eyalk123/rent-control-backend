"""owners.lock_notice_ack_plan — the one-time over-limit explanation

Revision ID: 063
Revises: 062
Create Date: 2026-09-21

When a subscription lapses or is downgraded, properties over the new ceiling become
read-only. That needs explaining once, in the app, rather than leaving someone to discover
it by finding a save button that no longer works.

The column stores the **plan** the explanation was last acknowledged for, not a boolean.
A flag would show the notice once and stay silent forever, but a landlord who acknowledges
on the free plan, subscribes again, and later downgrades to a different band is in a new
situation with a different number of locked properties — and deserves telling again.
Comparing plans gets that for free; comparing a flag cannot.

NULL means never acknowledged, which is the correct starting state for every existing row:
nobody has been shown anything yet.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "063"
down_revision: Union[str, None] = "062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "owners", sa.Column("lock_notice_ack_plan", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("owners", "lock_notice_ack_plan")

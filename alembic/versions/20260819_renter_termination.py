"""renter_termination — close a lease early without rewriting it

A lease could previously only be ended by editing lease_start or shortening
lease_years. Both are destructive: changing the start date re-anchors the frozen
cpi_base_index, and dropping years rewrites the schedule that past transactions
were priced against.

terminated_on records the early exit *alongside* the signed lease instead. The
lease_years blob, cpi_base_index and every transaction stay exactly as they were,
so historical reports still reconstruct. NULL means "not terminated", so every
existing row keeps its current behaviour with no backfill.

Revision ID: 043
Revises: 042
Create Date: 2026-08-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "043"
down_revision: Union[str, None] = "042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("renters", sa.Column("terminated_on", sa.Date(), nullable=True))
    op.add_column("renters", sa.Column("termination_reason", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("renters", "termination_reason")
    op.drop_column("renters", "terminated_on")

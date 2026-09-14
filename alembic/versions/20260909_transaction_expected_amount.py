"""transaction_expected_amount — what the lease said was owed, frozen at record time

A revenue row records what was *paid*. What was *owed* for that month was never stored,
so the payment grid re-derived it from the renter's current `lease_years` every time it
rendered. That is correct only for as long as the schedule stays still.

It does not. Editing a renter's base rent or escalation value re-derives every period from
the formula — elapsed ones included — so a lease raised from 5,000 to 5,500 retroactively
reprices last year, and twelve months that were paid exactly right start showing as
short. A mid-period manual change has the same effect for a different reason: one amount
per period is the only shape the schedule has, so the change can only be entered by
rewriting the period the paid months sit in.

CPI leases never had this problem because their amounts carry provenance — a `cpi_reading`
per period, and `is_frozen` refuses to recompute a period that has started. This column is
the same idea moved onto the payment: the amount the schedule quoted when the payment was
recorded, written once and never recalculated.

NULL means "recorded before this existed" and falls back to the live schedule, which is
exactly today's behaviour — so there is nothing to backfill and no row changes meaning.

Revision ID: 047
Revises: 046
Create Date: 2026-09-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "047"
down_revision: Union[str, None] = "046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("expected_amount", sa.Numeric(precision=12, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transactions", "expected_amount")

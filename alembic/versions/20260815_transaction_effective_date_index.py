"""transaction_effective_date_index — index the date transaction lists sort by

Transaction lists now order by COALESCE(month_for, date_of_payment) DESC (the month
rent was paid *for*, falling back to the payment date on expenses). Without an index
on that expression every paginated page pays for a full sort — there was no index on
month_for at all, only ix_transactions_date_of_payment.

COALESCE over two immutable date columns is immutable, so Postgres can index it.

Revision ID: 042
Revises: 041
Create Date: 2026-08-15

"""
from typing import Sequence, Union

from alembic import op

revision: str = "042"
down_revision: Union[str, None] = "041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_transactions_owner_effective_date "
        "ON transactions (owner_id, (COALESCE(month_for, date_of_payment)) DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_transactions_owner_effective_date")

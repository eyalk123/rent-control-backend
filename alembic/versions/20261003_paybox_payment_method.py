"""paybox_payment_method — add paybox to the payment method enum

PayBox is an Israeli payment app, so like `bit` it is offered only where the country has the
`bit_payments` capability; the gating lives in the clients, not in the storage.

`ALTER TYPE ... ADD VALUE` cannot run inside a transaction block on older PostgreSQL, and
Alembic wraps migrations in one, so the statement gets its own connection via
`autocommit_block`. `IF NOT EXISTS` keeps the migration re-runnable.

Revision ID: 066
Revises: 065
Create Date: 2026-10-03

"""
from typing import Sequence, Union

from alembic import op

revision: str = "066"
down_revision: Union[str, None] = "065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE paymentmethodenum ADD VALUE IF NOT EXISTS 'paybox'")


def downgrade() -> None:
    # A no-op for the same reason as 051: dropping an enum value means recreating the type
    # and rewriting every column that references it, which fails once any row uses it.
    pass

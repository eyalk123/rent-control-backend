"""penthouse_property_type — add penthouse to the property type enum

Offered in every country, so it is not gated on any capability.

`ALTER TYPE ... ADD VALUE` cannot run inside a transaction block on older PostgreSQL, and
Alembic wraps migrations in one, so the statement gets its own connection via
`autocommit_block`. `IF NOT EXISTS` keeps the migration re-runnable.

Revision ID: 065
Revises: 064
Create Date: 2026-10-03

"""
from typing import Sequence, Union

from alembic import op

revision: str = "065"
down_revision: Union[str, None] = "064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE propertytypeenum ADD VALUE IF NOT EXISTS 'penthouse'")


def downgrade() -> None:
    # A no-op for the same reason as 051: dropping an enum value means recreating the type
    # and rewriting every column that uses it, which fails once any row holds it.
    pass

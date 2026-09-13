"""global_enum_values — property types and payment methods the rest of the world uses

Adds three property types (condo_townhouse, room, other) and three payment methods
(card, mobile_payment, other). Nothing is removed: `garden_apartment`, `housing_unit` and
`bit` stay in the type and keep working, gated to Israel by capability rather than deleted.

**Additive on purpose.** A PostgreSQL enum value cannot be dropped without rewriting the
type and every column using it, and existing Israeli rows hold all three of those values.
The skimmed set is produced by *filtering* what the clients offer and what the API accepts,
not by narrowing the storage.

`ALTER TYPE ... ADD VALUE` cannot run inside a transaction block on older PostgreSQL, and
Alembic wraps migrations in one, so each statement gets its own connection via
`autocommit_block`. `IF NOT EXISTS` keeps the migration re-runnable.

Revision ID: 051
Revises: 050
Create Date: 2026-09-13

"""
from typing import Sequence, Union

from alembic import op

revision: str = "051"
down_revision: Union[str, None] = "050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROPERTY_TYPES = ("condo_townhouse", "room", "other")
_PAYMENT_METHODS = ("card", "mobile_payment", "other")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in _PROPERTY_TYPES:
            op.execute(f"ALTER TYPE propertytypeenum ADD VALUE IF NOT EXISTS '{value}'")
        for value in _PAYMENT_METHODS:
            op.execute(f"ALTER TYPE paymentmethodenum ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Deliberately a no-op. Removing an enum value means recreating the type and rewriting
    # every column that references it, which would fail outright the moment a single row
    # used one of these — and a downgrade that destroys data is worse than one that leaves
    # an unused value behind.
    pass

"""account_and_property_country — where the account is, and where each building is

Foundation for global support. Two columns, both backfilled to ``IL``, no behaviour change.

**Why the country is stored twice.** ``owners.country`` is what the user picks once at
signup; it drives currency, formats and the default for new properties.
``properties.country`` is copied from it at creation and is **the value every rule reads**,
because rules attach to where the building is, not to where the account holder signed up.

Today those are always the same value and the property form has no country picker, so this
looks redundant. It is one nullable column now. If it lived only on the account and a
single user ever added a foreign property, the fix would be a migration touching leases,
frozen index anchors and already-exported reports — with no correct answer for a lease that
had been priced under the wrong country's rules. This mirrors ``property_owner``, which is
per-property and load-bearing even though most accounts only ever have one.

**Nullable, not NOT NULL.** ``owners.country`` has to distinguish "not chosen yet" from
"chosen", because that is exactly what the signup country gate keys off. Making it NOT NULL
would need a sentinel value, and a sentinel that means "unset" is worse than NULL.
``properties.country`` is nullable for symmetry and because the owners row is written
best-effort (see ``get_current_owner``), so a property can in principle be created before
its account row exists. ``country_service.config_for`` resolves NULL to Israel — every row
that predates this column was Israeli by definition.

Revision ID: 048
Revises: 047
Create Date: 2026-09-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "048"
down_revision: Union[str, None] = "047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("owners", sa.Column("country", sa.String(length=2), nullable=True))
    op.add_column("properties", sa.Column("country", sa.String(length=2), nullable=True))

    # Every account and every building that exists today is Israeli. Backfilling rather
    # than leaving NULL means the country gate never fires for an existing user, which is
    # the whole "no existing user sees any change" requirement.
    op.execute("UPDATE owners SET country = 'IL' WHERE country IS NULL")
    op.execute("UPDATE properties SET country = 'IL' WHERE country IS NULL")


def downgrade() -> None:
    op.drop_column("properties", "country")
    op.drop_column("owners", "country")

"""item_colors — a color on every property, renter and expense category

Adds a nullable ``color`` column (one of ``app.entity_colors.ENTITY_COLORS``, stored as the
key) to ``properties``, ``renters`` and ``expense_categories``, and fills it for every
existing row so no one opens the app to a mix of colored and grey items:

* Built-in categories get the fixed color listed in ``BUILT_IN_COLORS``.
* Everything else gets what it would have been given had colors always existed: per owner,
  in creation order (``id``), the least-used color so far. Custom categories are spread
  against the built-ins the owner also sees, same as ``ExpenseCategoryService.create``.

The assignment rule is restated here rather than imported, so this migration keeps doing
what it did if the app's rule changes later.

Revision ID: 082
Revises: 081
Create Date: 2026-10-20

"""
from collections import Counter, defaultdict
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "082"
down_revision: Union[str, None] = "081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLORS = ("sky", "rose", "olive", "indigo", "plum", "mustard")

# Fifteen built-ins over six colors, two or three each, with an eye on the obvious pairings
# (water indigo, electricity and gardening olive, repairs mustard).
BUILT_IN_COLORS = {
    "property_tax": "sky",
    "air_conditioning": "sky",
    "management_fee": "sky",
    "maintenance": "rose",
    "gas": "rose",
    "legal_professional": "rose",
    "electricity": "olive",
    "gardening": "olive",
    "water": "indigo",
    "cleaning": "indigo",
    "building_fees": "indigo",
    "insurance": "plum",
    "mortgage_interest": "plum",
    "repairs": "mustard",
    "other": "mustard",
}


def _least_used(counts: Counter) -> str:
    return min(COLORS, key=lambda c: (counts[c], COLORS.index(c)))


def _backfill_by_owner(bind, table: str, start: dict[str, Counter] | None = None) -> None:
    rows = bind.execute(
        sa.text(f"SELECT id, owner_id FROM {table} WHERE color IS NULL ORDER BY id")
    ).fetchall()
    counts: dict[str, Counter] = defaultdict(Counter)
    for owner, counter in (start or {}).items():
        counts[owner] = Counter(counter)
    for row_id, owner_id in rows:
        color = _least_used(counts[owner_id])
        counts[owner_id][color] += 1
        bind.execute(
            sa.text(f"UPDATE {table} SET color = :color WHERE id = :id"),
            {"color": color, "id": row_id},
        )


def upgrade() -> None:
    for table in ("properties", "renters", "expense_categories"):
        op.add_column(table, sa.Column("color", sa.String(16), nullable=True))

    bind = op.get_bind()

    for key, color in BUILT_IN_COLORS.items():
        bind.execute(
            sa.text(
                "UPDATE expense_categories SET color = :color "
                "WHERE key = :key AND owner_id IS NULL"
            ),
            {"color": color, "key": key},
        )
    # Any built-in added later without an entry above still gets a color.
    built_in = Counter(
        r[0]
        for r in bind.execute(
            sa.text(
                "SELECT color FROM expense_categories "
                "WHERE owner_id IS NULL AND is_active = true AND color IS NOT NULL"
            )
        )
    )
    for (row_id,) in bind.execute(
        sa.text("SELECT id FROM expense_categories WHERE owner_id IS NULL AND color IS NULL ORDER BY id")
    ).fetchall():
        color = _least_used(built_in)
        built_in[color] += 1
        bind.execute(
            sa.text("UPDATE expense_categories SET color = :color WHERE id = :id"),
            {"color": color, "id": row_id},
        )

    _backfill_by_owner(bind, "properties")
    _backfill_by_owner(bind, "renters")
    custom_owners = [
        r[0]
        for r in bind.execute(
            sa.text("SELECT DISTINCT owner_id FROM expense_categories WHERE owner_id IS NOT NULL")
        )
    ]
    _backfill_by_owner(
        bind, "expense_categories", start={owner: built_in for owner in custom_owners}
    )


def downgrade() -> None:
    for table in ("expense_categories", "renters", "properties"):
        op.drop_column(table, "color")

"""global_expense_categories — the three categories a non-Israeli landlord needs

Adds ``mortgage_interest``, ``building_fees`` and ``legal_professional`` to the built-in
set, bringing it from twelve to fifteen.

**Read this before changing it.** A category can never be renamed or deleted — not the
built-ins, not custom ones (``PLATFORM.md`` §12). Built-ins are *global rows*
(``owner_id IS NULL``, unique ``key``) shared by every account, so there is no per-country
set and no way to give Israel a different twelve. These three therefore appear on every
account, Israeli ones included, permanently. That was the decision: a shared list with
three extra entries an Israeli landlord can ignore beats adding a country dimension to a
table that has never had one.

Mortgage interest and building fees are the two largest deductions a landlord outside
Israel would look for and not find. Legal & professional was a judgement call — the easiest
to drop now and the hardest to add later.

The names live in the clients' locale files and in ``report_service.CATEGORY_LABELS``,
keyed by ``key``; only user-created categories carry a ``name`` in the database.

Revision ID: 049
Revises: 048
Create Date: 2026-09-13

"""
from typing import Sequence, Union

from alembic import op

revision: str = "049"
down_revision: Union[str, None] = "048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `created_at` / `updated_at` must be supplied explicitly. They are NOT NULL with a
    # *Python-side* default (`default=utc_now_naive`), not a server default, so SQL that
    # does not name them fails — which is why the 2025 seed migrations omit them and this
    # one cannot: those ran before revision 037 added the columns.
    #
    # `timezone('utc', now())` rather than `now()`: the columns are TIMESTAMP WITHOUT TIME
    # ZONE holding UTC (see `app/clock.py`), and bare `now()` would store the database
    # server's local wall-clock instead, off by its UTC offset.
    #
    # ON CONFLICT because seeds here are idempotent by convention — re-running against a
    # database that already has the rows must not fail.
    op.execute(
        """
        INSERT INTO expense_categories (key, is_active, sort_order, created_at, updated_at)
        VALUES
        ('mortgage_interest', true, 13, timezone('utc', now()), timezone('utc', now())),
        ('building_fees', true, 14, timezone('utc', now()), timezone('utc', now())),
        ('legal_professional', true, 15, timezone('utc', now()), timezone('utc', now()))
        ON CONFLICT ON CONSTRAINT uq_expense_categories_key DO NOTHING
        """
    )


def downgrade() -> None:
    # Safe only because this migration is the only thing that could have created them and
    # nothing can have been filed against them yet. Categories are never deleted in normal
    # operation; this exists so the migration is reversible during development.
    op.execute(
        """
        DELETE FROM expense_categories
        WHERE key IN ('mortgage_interest', 'building_fees', 'legal_professional')
          AND owner_id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM transactions t WHERE t.category_id = expense_categories.id
          )
        """
    )

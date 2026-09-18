"""renter_open_ended — a lease that has no agreed end

Most of Europe rents this way: the tenancy rolls until an event stops it, and England
abolished fixed assured tenancies on 1 May 2026, so there it is now every residential
tenancy. Spain reaches the same place by a different route — the contract names a date, but
the tenant may require renewal up to five years, so that date is the earliest the tenancy
*could* end rather than when it will.

The product could not say this. Every date derives from a lease start plus a finite list of
periods, so the landlord invented an end date and muted the alert that counted down to it
(revision 052, which is this column's direct precedent in every respect).

What this column turns on is a **rolling five-year window**: a nightly job keeps five future
periods on the lease, appending one whenever an anniversary passes. Deliberately NOT a null
`lease_end` — `renter_repository.effective_lease_end()` is `coalesce(terminated_on,
lease_end)`, and `get_active`, `get_overdue_this_month` and `get_by_property_id` all compare
it `>= today`. NULL evaluates to NULL in SQL, so a null-ended renter would silently drop out
of every "active tenant" query. Keeping the window full means none of those change.

Per lease, not per country: an Israeli month-to-month holdover is open-ended too. The
country only decides the switch's default.

NOT NULL with a server default rather than nullable, for revision 052's reason: "unset" and
"false" mean the same thing here, and a nullable boolean would make every read carry an
`IS NOT TRUE` instead of a plain comparison.

Revision ID: 058
Revises: 057
Create Date: 2026-09-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "058"
down_revision: Union[str, None] = "057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "renters",
        sa.Column(
            "open_ended",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("renters", "open_ended")

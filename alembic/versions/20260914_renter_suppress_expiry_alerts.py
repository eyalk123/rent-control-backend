"""renter_suppress_expiry_alerts — "don't warn me when this lease expires"

The lease-expiring alert is the one part of the product that actively misbehaves for an
open-ended tenancy. Those countries have no end date, but the model requires one, so the
landlord types an estimate — and the app then counts down to a date they invented and
pushes at 90, 60 and 30 days out. This switches that off for one renter.

**Not gated on a country.** It is offered everywhere, because Israel has the same case: a
month-to-month holdover, where the tenant stays past the term with no new contract, produces
exactly the same meaningless countdown. Gating the switch would also cost more than shipping
it — the column and the query condition are identical either way, and only the UI would need
the extra conditional.

NOT NULL with a server default rather than nullable: "unset" and "false" mean the same thing
here, and a nullable boolean would make every read carry an `IS NOT TRUE` instead of a plain
comparison.

Revision ID: 052
Revises: 051
Create Date: 2026-09-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "052"
down_revision: Union[str, None] = "051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "renters",
        sa.Column(
            "suppress_expiry_alerts",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("renters", "suppress_expiry_alerts")

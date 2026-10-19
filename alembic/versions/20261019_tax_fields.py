"""Tax tracks fields: company owner, birth date, over 60, company renter, renovation

What the tax tracks report needs to know and cannot read from anything already stored.
Every one starts off or empty, so existing rows mean what they meant before.

- property_owners.is_company — a company is outside the individual tracks altogether.
- property_owners.birth_date / over_60 / over_60_marked_on — the 60+ brackets. The tick is
  for an owner who would rather not give a date; it counts from the year it was ticked, so
  the server records when.
- renters.is_company — a company renter rules out the exemption, not the 10% track.
- transactions.is_renovation — a renovation is not a deductible repair.

Revision ID: 078
Revises: 077
Create Date: 2026-10-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "078"
down_revision: Union[str, None] = "077"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _flag(name: str) -> sa.Column:
    return sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.text("false"))


def upgrade() -> None:
    op.add_column("property_owners", _flag("is_company"))
    op.add_column("property_owners", sa.Column("birth_date", sa.Date(), nullable=True))
    op.add_column("property_owners", _flag("over_60"))
    op.add_column("property_owners", sa.Column("over_60_marked_on", sa.Date(), nullable=True))
    op.add_column("renters", _flag("is_company"))
    op.add_column("transactions", _flag("is_renovation"))


def downgrade() -> None:
    op.drop_column("transactions", "is_renovation")
    op.drop_column("renters", "is_company")
    op.drop_column("property_owners", "over_60_marked_on")
    op.drop_column("property_owners", "over_60")
    op.drop_column("property_owners", "birth_date")
    op.drop_column("property_owners", "is_company")

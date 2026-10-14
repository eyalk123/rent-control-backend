"""owner_timezone — the landlord's own calendar for business "today"

Overdue rent, expiring leases and reminders used to be judged on the server's date, which is
UTC: in New York that date rolls over at 8pm, so rent turned "overdue" the evening before it
was due. Each client now reports its IANA zone on every request (`X-Client-Timezone`) and it
is kept here. No backfill: NULL reads as UTC, which is exactly what every account had until
now, and the first request from an updated client fills it in.

Revision ID: 071
Revises: 070
Create Date: 2026-10-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "071"
down_revision: Union[str, None] = "070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("owners", sa.Column("timezone", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("owners", "timezone")

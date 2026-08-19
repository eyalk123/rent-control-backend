"""lease_contract_end — the date the *binding* term ends

`lease_end` covers the whole signed schedule, options included, and is what every
"is this renter active" query keys off: an option year is still a year the tenant
may be living there and owing rent, so that meaning has to stay.

But it is the wrong date to *show* and to warn about. An option year is not yet
exercised, so the moment the landlord actually has to decide something is when the
contract periods run out — which is also what both clients have always displayed
(`getLeaseEndDate` counts contract years only). The two disagreed: a 2+1 lease read
"ends 2028" in the UI while its expiry alert fired off 2029.

contract_end stores that date so `get_expiring_leases` can filter on it in SQL.
Backfilled from the existing rows; recomputed on every write thereafter.

Revision ID: 044
Revises: 043
Create Date: 2026-08-19

"""
import json
from datetime import date, datetime
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op
from dateutil.relativedelta import relativedelta

revision: str = "044"
down_revision: Union[str, None] = "043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _as_date(value) -> Optional[date]:
    """DBAPI drivers disagree about DATE columns — psycopg2 hands back a `date`, others a
    string. Parse defensively so the backfill can't die halfway through a migration."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def upgrade() -> None:
    op.add_column("renters", sa.Column("contract_end", sa.Date(), nullable=True))

    # Backfill in Python rather than SQL: the term lives inside a JSON blob, and the
    # absent-months default is a rule this migration should not re-implement in SQL.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, lease_start, lease_years FROM renters WHERE lease_start IS NOT NULL")
    ).fetchall()
    for row in rows:
        lease_start = _as_date(row.lease_start)
        if lease_start is None:
            continue
        try:
            years = json.loads(row.lease_years) if row.lease_years else []
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(years, list) or not years:
            continue
        months = 0
        last_contract_end = None
        for year in years:
            if not isinstance(year, dict):
                continue
            raw = year.get("months")
            months += raw if isinstance(raw, int) and raw > 0 else 12
            if year.get("type") != "option":
                last_contract_end = months
        if last_contract_end is None:
            continue
        conn.execute(
            sa.text("UPDATE renters SET contract_end = :d WHERE id = :i"),
            {"d": lease_start + relativedelta(months=last_contract_end), "i": row.id},
        )


def downgrade() -> None:
    op.drop_column("renters", "contract_end")

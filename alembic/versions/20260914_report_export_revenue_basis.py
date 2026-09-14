"""report_export_revenue_basis — which recognition basis each export used

The basis is chosen per report, alongside the language, rather than stored as an account
setting. That was deliberate: a stored preference silently re-interprets history, so the
same 2025 report would say two different things depending on when it was generated. A
per-report choice changes nothing retroactively.

Recording it here is what makes the history list honest — two PDFs for the same year that
differ by a month's rent, with nothing distinguishing them, is exactly the confusion this
feature could otherwise create.

NULL means accrual. Every row that predates this column was generated on the only basis
that existed, so there is nothing to backfill and no row changes meaning.

Revision ID: 054
Revises: 053
Create Date: 2026-09-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "054"
down_revision: Union[str, None] = "053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "report_exports",
        sa.Column("revenue_basis", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("report_exports", "revenue_basis")

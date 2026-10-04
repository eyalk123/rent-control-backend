"""receipt_scans — tell lease scans and receipt scans apart on the extraction log

`document_extraction_logs` held only lease scans, and the free plan's monthly allowance is
counted from it. Receipt scans now land in the same table, with their own allowance, so each
row says which it was. Every existing row is a lease scan, which is what the server default
records for them.

`created_transaction_id` is the receipt counterpart of `created_property_id` /
`created_renter_id`: the expense the reviewed form was saved as.

Revision ID: 067
Revises: 066
Create Date: 2026-10-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "067"
down_revision: Union[str, None] = "066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_extraction_logs",
        sa.Column("kind", sa.String(), nullable=False, server_default="lease"),
    )
    op.add_column(
        "document_extraction_logs",
        sa.Column(
            "created_transaction_id",
            sa.Integer(),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("document_extraction_logs", "created_transaction_id")
    op.drop_column("document_extraction_logs", "kind")

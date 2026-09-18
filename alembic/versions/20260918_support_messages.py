"""support_messages — in-app bug reports, questions and suggestions

Revision ID: 059
Revises: 058
Create Date: 2026-09-18

The table is a durable record, not a delivery mechanism: submissions are emailed
to the product owner, and this row is what survives if that send fails. It holds
no screenshot bytes and no screenshot URLs — images travel base64 in the request,
go out as email attachments and are never persisted — so the only lasting copy of
a screenshot full of renter PII is the one in the recipient's mailbox.

The composite index serves the per-owner hourly rate limit, which counts this
owner's rows in the last hour. That count is done in the database rather than in
process memory because the API runs on several Railway replicas, and an in-memory
counter would let through one burst per replica.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "059"
down_revision: Union[str, None] = "058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "support_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column(
            "type",
            sa.Enum("bug", "question", "suggestion", name="supportmessagetypeenum"),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("screenshot_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("client_app", sa.String(), nullable=True),
        sa.Column("client_platform", sa.String(), nullable=True),
        sa.Column("client_version", sa.String(), nullable=True),
        sa.Column("language", sa.String(length=5), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("email_sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_support_messages_owner_id"), "support_messages", ["owner_id"]
    )
    op.create_index(
        "ix_support_messages_owner_created",
        "support_messages",
        ["owner_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_support_messages_owner_created", table_name="support_messages")
    op.drop_index(op.f("ix_support_messages_owner_id"), table_name="support_messages")
    op.drop_table("support_messages")
    sa.Enum(name="supportmessagetypeenum").drop(op.get_bind(), checkfirst=True)

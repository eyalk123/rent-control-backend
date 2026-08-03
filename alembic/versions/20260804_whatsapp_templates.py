"""whatsapp_templates — per-owner overrides for the WhatsApp message copy

Revision ID: 041
Revises: 040
Create Date: 2026-08-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "041"
down_revision: Union[str, None] = "040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default so existing rows land on "no overrides" rather than NULL — every
    # owner who has ever opened notification settings already has a row here.
    op.add_column(
        "notification_settings",
        sa.Column(
            "whatsapp_templates", sa.Text(), nullable=False, server_default="{}"
        ),
    )


def downgrade() -> None:
    op.drop_column("notification_settings", "whatsapp_templates")

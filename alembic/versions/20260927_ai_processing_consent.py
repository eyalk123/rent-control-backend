"""Add 'ai_processing' to legaldocumentenum

Revision ID: 064
Revises: 063
Create Date: 2026-09-27

Consent to send data to Anthropic for lease scanning and the assistant is recorded in
legal_acceptances as a third document, next to the Terms and the Privacy Policy. App Store
Guideline 5.1.2(i) requires explicit permission before personal data first reaches a
third-party AI, and storing it on the account (rather than the device) means it is asked
once per person, not once per phone.

"""
from typing import Sequence, Union

from alembic import op

revision: str = "064"
down_revision: Union[str, None] = "063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres cannot run ALTER TYPE ... ADD VALUE inside the transaction Alembic opens.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE legaldocumentenum ADD VALUE IF NOT EXISTS 'ai_processing'")


def downgrade() -> None:
    # Postgres has no "DROP VALUE" for an enum; see 20260712_add_check_payment_method.py.
    pass

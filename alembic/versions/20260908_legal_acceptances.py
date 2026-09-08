"""legal_acceptances — which version of which legal document each owner agreed to

Revision ID: 046
Revises: 045
Create Date: 2026-09-08

Until now nothing recorded this. The sign-up form's "I accept" checkbox gated the submit
button and was then discarded, and the Google sign-in path never asked at all, so the only
evidence that anyone agreed to anything was that they had an account.

The table is append-only: revising a document means the owner accepts again, and that new
row must not overwrite what it supersedes. `owner_id` is a plain indexed String holding
the Firebase uid, matching every other owner-scoped table here — no FK to `owners`, which
is itself only a mirror of the auth provider.

`platform` reuses the existing `deviceplatformenum` type rather than declaring a second
one with the same three members; it is therefore NOT dropped on downgrade, since
`device_tokens` still needs it.

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM

revision: str = "046"
down_revision: Union[str, None] = "045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN CREATE TYPE legaldocumentenum AS ENUM ('terms', 'privacy'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )

    legal_document_enum = ENUM(
        "terms", "privacy", name="legaldocumentenum", create_type=False
    )
    device_platform_enum = ENUM(
        "ios", "android", "web", name="deviceplatformenum", create_type=False
    )

    op.create_table(
        "legal_acceptances",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("document", legal_document_enum, nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("locale", sa.String(), nullable=False),
        sa.Column("platform", device_platform_enum, nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_legal_acceptances_owner_id", "legal_acceptances", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_legal_acceptances_owner_id", table_name="legal_acceptances")
    op.drop_table("legal_acceptances")
    # deviceplatformenum is left alone on purpose — device_tokens still uses it.
    op.execute("DROP TYPE legaldocumentenum")

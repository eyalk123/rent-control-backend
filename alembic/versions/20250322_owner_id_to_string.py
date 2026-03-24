"""Change owner_id columns from Integer to String for Clerk user IDs

Revision ID: 008
Revises: 007
Create Date: 2025-03-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "properties",
        "owner_id",
        existing_type=sa.Integer(),
        type_=sa.String(),
        existing_nullable=False,
        postgresql_using="owner_id::varchar",
    )
    op.alter_column(
        "suppliers",
        "owner_id",
        existing_type=sa.Integer(),
        type_=sa.String(),
        existing_nullable=False,
        postgresql_using="owner_id::varchar",
    )
    op.alter_column(
        "expense_categories",
        "owner_id",
        existing_type=sa.Integer(),
        type_=sa.String(),
        existing_nullable=True,
        postgresql_using="owner_id::varchar",
    )


def downgrade() -> None:
    op.alter_column(
        "expense_categories",
        "owner_id",
        existing_type=sa.String(),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="owner_id::integer",
    )
    op.alter_column(
        "suppliers",
        "owner_id",
        existing_type=sa.String(),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="owner_id::integer",
    )
    op.alter_column(
        "properties",
        "owner_id",
        existing_type=sa.String(),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="owner_id::integer",
    )

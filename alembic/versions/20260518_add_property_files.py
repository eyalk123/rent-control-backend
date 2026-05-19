"""add property_files table

Revision ID: 019
Revises: a983b18bc38a
Create Date: 2026-05-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "019"
down_revision: Union[str, None] = "a983b18bc38a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "property_files",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("property_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_property_files_property_id", "property_files", ["property_id"])


def downgrade() -> None:
    op.drop_index("ix_property_files_property_id", table_name="property_files")
    op.drop_table("property_files")

"""add document_extraction_logs table

Revision ID: 029
Revises: 028
Create Date: 2026-06-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_extraction_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        # file metadata
        sa.Column("filename", sa.String(), nullable=True),
        sa.Column("content_type", sa.String(), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        # ai call
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 6), nullable=True),
        # extraction quality
        sa.Column("fields_extracted", sa.Integer(), nullable=True),
        sa.Column("low_confidence_count", sa.Integer(), nullable=True),
        sa.Column("medium_confidence_count", sa.Integer(), nullable=True),
        # outcome
        sa.Column("forms_submitted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("created_property_id", sa.Integer(), nullable=True),
        sa.Column("created_renter_id", sa.Integer(), nullable=True),
        sa.Column("contract_url", sa.Text(), nullable=True),
        sa.Column("fields_given_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fields_changed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("field_edits", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["created_property_id"], ["properties.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_renter_id"], ["renters.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_extraction_logs_owner_id", "document_extraction_logs", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_document_extraction_logs_owner_id", table_name="document_extraction_logs")
    op.drop_table("document_extraction_logs")

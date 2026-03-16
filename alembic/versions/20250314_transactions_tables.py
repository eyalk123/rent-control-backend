"""Transactions, expense_categories, suppliers

Revision ID: 003
Revises: 002
Create Date: 2025-03-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM


revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("currency_code", sa.String(), nullable=True))

    op.create_table(
        "expense_categories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_expense_categories_key"),
    )

    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["category_id"], ["expense_categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.execute(
        "DO $$ BEGIN CREATE TYPE transactiontypeenum AS ENUM ('revenue', 'expense'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    op.execute(
        "DO $$ BEGIN CREATE TYPE paymentmethodenum AS ENUM ('bit', 'cash', 'bank_transfer'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )

    transaction_type_enum = ENUM(
        "revenue", "expense", name="transactiontypeenum", create_type=False
    )
    payment_method_enum = ENUM(
        "bit", "cash", "bank_transfer", name="paymentmethodenum", create_type=False
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("type", transaction_type_enum, nullable=False),
        sa.Column("property_id", sa.Integer(), nullable=False),
        sa.Column("renter_id", sa.Integer(), nullable=True),
        sa.Column("payment_method", payment_method_enum, nullable=True),
        sa.Column("date_of_payment", sa.Date(), nullable=False),
        sa.Column("month_for", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency_code", sa.String(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("supplier_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["renter_id"], ["renters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["category_id"], ["expense_categories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transactions_type", "transactions", ["type"], unique=False)
    op.create_index("ix_transactions_property_id", "transactions", ["property_id"], unique=False)
    op.create_index("ix_transactions_renter_id", "transactions", ["renter_id"], unique=False)
    op.create_index(
        "ix_transactions_date_of_payment", "transactions", ["date_of_payment"], unique=False
    )
    op.create_index(
        "ix_transactions_property_type_date",
        "transactions",
        ["property_id", "type", "date_of_payment"],
        unique=False,
    )

    # Seed expense_categories
    op.execute(
        """
        INSERT INTO expense_categories (key, is_active, sort_order) VALUES
        ('electricity', true, 1),
        ('air_conditioning', true, 2),
        ('water', true, 3),
        ('maintenance', true, 4),
        ('insurance', true, 5),
        ('property_tax', true, 6),
        ('other', true, 7)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_property_type_date", table_name="transactions")
    op.drop_index("ix_transactions_date_of_payment", table_name="transactions")
    op.drop_index("ix_transactions_renter_id", table_name="transactions")
    op.drop_index("ix_transactions_property_id", table_name="transactions")
    op.drop_index("ix_transactions_type", table_name="transactions")
    op.drop_table("transactions")
    op.execute("DROP TYPE paymentmethodenum")
    op.execute("DROP TYPE transactiontypeenum")
    op.drop_table("suppliers")
    op.drop_table("expense_categories")
    op.drop_column("properties", "currency_code")

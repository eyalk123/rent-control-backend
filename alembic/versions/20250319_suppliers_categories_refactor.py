"""Suppliers and expense categories refactoring

Revision ID: 006
Revises: 005
Create Date: 2025-03-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1.1 expense_categories: add name, owner_id; make key nullable
    op.add_column(
        "expense_categories",
        sa.Column("name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "expense_categories",
        sa.Column("owner_id", sa.Integer(), nullable=True),
    )
    op.alter_column(
        "expense_categories",
        "key",
        existing_type=sa.String(),
        nullable=True,
    )

    # 1.2 suppliers: add owner_id
    op.add_column(
        "suppliers",
        sa.Column("owner_id", sa.Integer(), nullable=True),
    )
    # Backfill owner_id for existing suppliers (use 1 as default)
    op.execute("UPDATE suppliers SET owner_id = 1 WHERE owner_id IS NULL")
    op.alter_column(
        "suppliers",
        "owner_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    # 1.3 supplier_categories junction table
    op.create_table(
        "supplier_categories",
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["expense_categories.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("supplier_id", "category_id"),
    )

    # Migrate existing category_id to supplier_categories
    op.execute(
        """
        INSERT INTO supplier_categories (supplier_id, category_id)
        SELECT id, category_id FROM suppliers WHERE category_id IS NOT NULL
        """
    )

    # Drop category_id from suppliers
    op.drop_constraint(
        "suppliers_category_id_fkey",
        "suppliers",
        type_="foreignkey",
    )
    op.drop_column("suppliers", "category_id")

    # 1.4 Expand seed expense categories (add missing predefined ones)
    op.execute(
        """
        INSERT INTO expense_categories (key, is_active, sort_order) VALUES
        ('gas', true, 8),
        ('repairs', true, 9),
        ('cleaning', true, 10),
        ('gardening', true, 11),
        ('management_fee', true, 12)
        ON CONFLICT ON CONSTRAINT uq_expense_categories_key DO NOTHING
        """
    )


def downgrade() -> None:
    # Restore category_id to suppliers
    op.add_column(
        "suppliers",
        sa.Column("category_id", sa.Integer(), nullable=True),
    )
    # Migrate first category from supplier_categories back to category_id
    op.execute(
        """
        UPDATE suppliers s SET category_id = (
            SELECT MIN(sc.category_id) FROM supplier_categories sc WHERE sc.supplier_id = s.id
        )
        """
    )
    op.alter_column(
        "suppliers",
        "category_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_foreign_key(
        "suppliers_category_id_fkey",
        "suppliers",
        "expense_categories",
        ["category_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_table("supplier_categories")

    op.drop_column("suppliers", "owner_id")

    op.alter_column(
        "expense_categories",
        "key",
        existing_type=sa.String(),
        nullable=False,
    )
    op.drop_column("expense_categories", "owner_id")
    op.drop_column("expense_categories", "name")

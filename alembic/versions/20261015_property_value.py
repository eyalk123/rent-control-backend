"""property_value — purchase date, and a dated history of what the property is worth

`properties.purchase_price` has existed since the first migration, but neither client has
shown it since April 2026, so it comes back now with a `purchase_date` beside it.

The value is a separate table rather than a column: an estimate without a date goes stale
silently, and each one the owner records is kept so the apps can show how it moved. The
current value is the row with the latest `valued_on`.

Stored purchase prices of exactly 0 become NULL. The mobile app used to send 0 when the field
was left blank (`purchase_price ?? 0`), so a zero is "not entered", not a price — and now
that the field is back on screen it would otherwise show every such property as bought for
nothing.

Revision ID: 072
Revises: 071
Create Date: 2026-10-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "072"
down_revision: Union[str, None] = "071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("purchase_date", sa.Date(), nullable=True))
    op.execute("UPDATE properties SET purchase_price = NULL WHERE purchase_price = 0")
    op.create_table(
        "property_valuations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "property_id",
            sa.Integer(),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("valued_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_property_valuations_property_id", "property_valuations", ["property_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_property_valuations_property_id", table_name="property_valuations")
    op.drop_table("property_valuations")
    # The zeroed purchase prices are not restored: NULL and 0 meant the same thing.
    op.drop_column("properties", "purchase_date")

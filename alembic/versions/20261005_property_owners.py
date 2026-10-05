"""property_owners — the human owner of a property becomes a record with contact details

`properties.property_owner` was free text, and it was load-bearing: reports group by it,
notification rules scope to it, the revenue form filters by it. A typo made a second owner.
It becomes a `property_owners` row (name, phone, email, bank account, notes — the same
shape as a supplier), and the property points at it.

Every distinct name an account typed becomes one row, **exactly as typed** — "Dad" and "dad"
stay two owners, for the user to merge; nothing here guesses that they are the same person.
A blank or whitespace-only name was already "no owner" everywhere it was read, so it maps to
none. Names stay unique per account, which is what keeps the name-based references
(notification scopes, report export history) pointing at one owner.

Revision ID: 069
Revises: 068
Create Date: 2026-10-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "069"
down_revision: Union[str, None] = "068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "property_owners",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("bank_account", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "name", name="uq_property_owners_owner_name"),
    )
    op.create_index("ix_property_owners_owner_id", "property_owners", ["owner_id"])

    op.add_column("properties", sa.Column("property_owner_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_properties_property_owner_id",
        "properties",
        "property_owners",
        ["property_owner_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_properties_property_owner_id", "properties", ["property_owner_id"])

    op.execute(
        """
        INSERT INTO property_owners (owner_id, name, is_active, created_at, updated_at)
        SELECT owner_id, property_owner, true, min(created_at), min(created_at)
        FROM properties
        WHERE property_owner IS NOT NULL AND btrim(property_owner) <> ''
        GROUP BY owner_id, property_owner
        """
    )
    op.execute(
        """
        UPDATE properties p
        SET property_owner_id = po.id
        FROM property_owners po
        WHERE po.owner_id = p.owner_id AND po.name = p.property_owner
        """
    )

    op.drop_column("properties", "property_owner")


def downgrade() -> None:
    op.add_column("properties", sa.Column("property_owner", sa.String(), nullable=True))
    op.execute(
        """
        UPDATE properties p
        SET property_owner = po.name
        FROM property_owners po
        WHERE po.id = p.property_owner_id
        """
    )
    op.drop_index("ix_properties_property_owner_id", table_name="properties")
    op.drop_constraint("fk_properties_property_owner_id", "properties", type_="foreignkey")
    op.drop_column("properties", "property_owner_id")
    op.drop_index("ix_property_owners_owner_id", table_name="property_owners")
    op.drop_table("property_owners")

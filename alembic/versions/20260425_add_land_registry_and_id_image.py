"""Add land_registry_url to properties and id_image_url to renters

Revision ID: 012
Revises: 011
Create Date: 2026-04-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("land_registry_url", sa.String(), nullable=True))
    op.add_column("renters", sa.Column("id_image_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("properties", "land_registry_url")
    op.drop_column("renters", "id_image_url")

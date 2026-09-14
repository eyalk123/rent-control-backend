"""optional_property_fields — postal code, floor area and purchase price stop being required

All three are NOT NULL today and the API rejects a property without them. Globally that is
wrong in two different ways:

- **Postal codes do not exist** in Ireland (outside Eircode), much of the Gulf, and large
  parts of Africa and the Caribbean. There is no value to type.
- **Requiring a purchase price** from someone who inherited a property, or who manages
  rather than owns it, is a signup killer — it asks for a number they may not have and may
  not want to record.

Made optional for **every** country, Israel included. Both clients already treat all three
as optional in their own form schemas (`zipCode: optionalString`, `sqFt:
optionalNumericString`, and `purchase_price` has defaulted to 0.0 for a while), so the
NOT NULL constraints were the only thing still enforcing them — and a listed friction point
either way. Nothing is removed and no existing row changes.

Revision ID: 053
Revises: 052
Create Date: 2026-09-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "053"
down_revision: Union[str, None] = "052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("properties", "zip_code", existing_type=sa.String(), nullable=True)
    op.alter_column("properties", "sq_ft", existing_type=sa.Integer(), nullable=True)
    op.alter_column("properties", "purchase_price", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    # Only reversible while no row has taken advantage of it. Backfilling a made-up postal
    # code or floor area to satisfy the constraint would be worse than failing loudly, so
    # this is left to fail if such a row exists.
    op.alter_column("properties", "purchase_price", existing_type=sa.Float(), nullable=False)
    op.alter_column("properties", "sq_ft", existing_type=sa.Integer(), nullable=False)
    op.alter_column("properties", "zip_code", existing_type=sa.String(), nullable=False)

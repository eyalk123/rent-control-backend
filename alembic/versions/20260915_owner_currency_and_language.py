"""owner_currency_and_language — the two choices that sit beside the country

Until now both were derived: currency was a pure function of ``owners.country``, and
language lived in device storage on each client. Neither held up.

**Currency** needed an override because the country is a good guess and not a fact — a
Swiss owner letting in euros, an Israeli whose lease is written in dollars. The storage
side was already right (``properties.currency_code`` is frozen at creation and every
transaction snapshots it), so this is the missing input, not a new mechanism.

**Language** needed a home that is not the device. It was ``localStorage['app_language']``
on web and the same key in ``AsyncStorage`` on mobile, so signing in on a second device
silently reverted it — unlike tour progress and the WhatsApp templates, which already live
on the account precisely so they follow the user.

**Both nullable, and there is no backfill.** NULL means "not chosen — use the country's
own", which is what every existing account is and what keeps them byte-for-byte unchanged:
``country_service.effective_currency`` resolves NULL to the country row, and the clients
fall back to the device. A backfill would freeze today's derived value into stored state
for no gain, and would then be indistinguishable from a deliberate choice.

``currency`` is 3 chars (ISO 4217). ``language`` is 5 to leave room for a regional tag
(``pt-BR``) without another migration, though only ``en`` and ``he`` exist today.

Revision ID: 057
Revises: 056
Create Date: 2026-09-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "057"
down_revision: Union[str, None] = "056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("owners", sa.Column("currency", sa.String(length=3), nullable=True))
    op.add_column("owners", sa.Column("language", sa.String(length=5), nullable=True))


def downgrade() -> None:
    op.drop_column("owners", "language")
    op.drop_column("owners", "currency")

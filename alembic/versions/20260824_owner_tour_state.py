"""tour_state — which onboarding tours and seeds an owner has already been shown

Onboarding is two-layer: a *seed* names a feature the user cannot see from where they
are standing, and the *tour* at the destination explains it. The two are tracked apart
on purpose — showing the seed must not consume the destination tour.

This lives on the owner rather than in device storage because it is one account across
web and mobile: a tour finished on the phone must not replay on the desktop, and a
reinstall must not restart the whole thing. Same JSON-in-Text shape as
``notification_settings.whatsapp_templates`` (revision 041).

    {
      "tours_seen":  {"first-run": "2026-08-24T10:00:00Z", ...},
      "seeds_shown": {"suppliers": "2026-08-24T10:00:00Z", ...},
      "tours_disabled": false
    }

server_default so every existing owner lands on "has seen nothing" rather than NULL —
which is correct: nobody has seen a tour, because none existed before this.

Revision ID: 045
Revises: 044
Create Date: 2026-08-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "045"
down_revision: Union[str, None] = "044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "owners",
        sa.Column("tour_state", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("owners", "tour_state")

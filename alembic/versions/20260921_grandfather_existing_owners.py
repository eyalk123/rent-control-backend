"""owners.granted_plan — a plan held outright, and the grandfather backfill

Revision ID: 061
Revises: 060
Create Date: 2026-09-21

Every landlord who already had an account when billing was introduced keeps top-tier
access permanently and for free. They built their portfolios on a product that made no
such demand, and introducing one retroactively would be a breach of that.

**The grant is on `owners`, not in `subscriptions`, and that placement is the point.**
Written as a subscription row it would sit in the path of webhook upserts, which replace
every field of the row they touch. A grandfathered landlord who later bought a plan and
then cancelled would have their grant quietly overwritten and lose access that was never
conditional on paying for anything. Here it is untouchable by billing, and entitlement
resolves to whichever of grant and subscription permits more
(`entitlement_service.better_plan`).

**The backfill is the cutoff.** Everyone in `owners` at the moment this migration runs is
grandfathered; everyone who signs up afterwards is not. That is deliberate — the alternative
is a hardcoded date that has to be guessed in advance and then defended — but it does mean
the boundary is "when this was deployed" rather than a date anyone chose. Accounts created
between deployment and launch will be on the normal free plan.

Downgrade clears the column rather than trying to remember who was granted what. The grant
is recoverable from a backup, and a downgrade that silently kept grants would leave the
schema and the data disagreeing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "061"
down_revision: Union[str, None] = "060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Kept as a literal rather than imported from app.services.entitlement_service: a
# migration records what happened at a point in time, and must not change meaning later
# because a constant was renamed.
GRANDFATHERED_PLAN = "tier_16_plus"


def upgrade() -> None:
    op.add_column("owners", sa.Column("granted_plan", sa.String(), nullable=True))
    op.execute(
        sa.text("UPDATE owners SET granted_plan = :plan").bindparams(
            plan=GRANDFATHERED_PLAN
        )
    )


def downgrade() -> None:
    op.drop_column("owners", "granted_plan")

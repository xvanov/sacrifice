"""add proof dispatch bookkeeping (dispatched_at, attempts, criteria snapshot)

Supports app/workers/reconcile_dispatch.py, which re-queues verification for
proofs whose Celery task never reached (or never completed in) the worker.

Backfill choice for pre-existing rows
-------------------------------------
``dispatch_attempts`` is backfilled to a large sentinel (999) for submissions
that are already ``pending``, which makes the reconciler ignore them: its
candidate predicate is ``dispatch_attempts < verification_dispatch_max_attempts``
(default 4). The naive backfill — leaving them at 0 — would make every
historical pending row instantly eligible on first boot and stampede the broker
with a verification burst.

Ignoring them is also the money-safe choice, not just the load-safe one. A
pre-existing pending row has no ``dispatch_criteria`` snapshot (the column did
not exist when it was written), so replaying it would verify against ``{}`` —
for github_repo that means no PAT, which fails verification, and
``persist_verification_result`` charges the pledge on failure. Re-verifying
historical rows could therefore bill users for proofs that were fine. An
operator can opt a specific row in deliberately by resetting its counter.

``dispatched_at`` is backfilled to ``submitted_at`` for those same rows purely
as provenance (they were dispatched at submission time under the old code).
Non-pending rows keep NULL/0: they are already resolved and never candidates.

Revision ID: d5e6f7a8b9c0
Revises: a1b2c3d4e5f6
Create Date: 2026-07-25 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Deliberately not tied to settings.verification_dispatch_max_attempts: a
# migration must not change meaning when configuration changes.
_INELIGIBLE_SENTINEL = 999


def upgrade() -> None:
    op.add_column(
        "proof_submissions",
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "proof_submissions",
        sa.Column(
            "dispatch_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "proof_submissions",
        sa.Column("dispatch_criteria", postgresql.JSONB, nullable=True),
    )

    # Existing pending rows: mark ineligible (see module docstring).
    op.execute(
        sa.text(
            f"""
            UPDATE proof_submissions
            SET dispatch_attempts = {_INELIGIBLE_SENTINEL},
                dispatched_at = submitted_at
            WHERE verification_status = 'pending'
            """
        )
    )

    # Partial index: the sweep only ever scans pending rows, which are a tiny
    # fraction of the table, ordered by submitted_at.
    op.execute(
        sa.text(
            """
            CREATE INDEX ix_proof_submissions_pending_dispatch
            ON proof_submissions (submitted_at, dispatch_attempts)
            WHERE verification_status = 'pending'
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_proof_submissions_pending_dispatch"))
    op.drop_column("proof_submissions", "dispatch_criteria")
    op.drop_column("proof_submissions", "dispatch_attempts")
    op.drop_column("proof_submissions", "dispatched_at")

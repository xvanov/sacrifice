"""add payment_failed to goal_status

``app/models/goal.py`` has declared ``payment_failed`` in the ``goal_status``
enum for a long time, but no revision ever added it to the database type. Every
environment that worked did so because its schema came from
``Base.metadata.create_all`` (pytest's conftest, and the hand-built dev/live
databases), which builds the enum from the model. A database provisioned purely
from migrations — which is now the case for the containerised stack, since the
compose files gained a ``migrate`` service — got the type WITHOUT that value.

The consequence is a hard failure on a real-money path: every charge-failure
branch in ``app/workers/payments.py`` (the no-card-on-file case, the
retry-exhausted case, and a declined PaymentIntent) sets
``goals.status = 'payment_failed'``, which raises
``invalid input value for enum goal_status: "payment_failed"`` on a
migration-built database. A declined card would crash the worker instead of
recording the failure.

``ALTER TYPE ... ADD VALUE`` is safe inside Alembic's transaction on
PostgreSQL 12+ so long as the new value is not *used* in the same transaction,
which it is not here. ``IF NOT EXISTS`` makes this a no-op on the databases
that already carry the value out-of-band, so it is safe to run everywhere.

Revision ID: a3c7d9e1f2b4
Revises: f2b3c4d5e6a7
Create Date: 2026-07-25

"""

from alembic import op
import sqlalchemy as sa


revision = "a3c7d9e1f2b4"
down_revision = "f2b3c4d5e6a7"
branch_labels = None
depends_on = None

_NEW_VALUE = "payment_failed"


def upgrade() -> None:
    op.execute(
        sa.text(f"ALTER TYPE goal_status ADD VALUE IF NOT EXISTS '{_NEW_VALUE}'")
    )


def downgrade() -> None:
    """Remove the value by recreating the type without it.

    PostgreSQL has no ``DROP VALUE``, so a genuine rollback means rebuilding
    the type and recasting the column. Any goal actually sitting in
    ``payment_failed`` is moved to ``failed``: that is the closest truthful
    resting state (the pledge was owed and not collected), and leaving the rows
    unmapped would make the cast fail outright.
    """
    op.execute(
        sa.text("UPDATE goals SET status = 'failed' WHERE status = 'payment_failed'")
    )
    op.execute(sa.text("ALTER TYPE goal_status RENAME TO goal_status_old"))
    op.execute(
        sa.text(
            "CREATE TYPE goal_status AS ENUM ("
            "'draft', 'active', 'pending_review', 'verified', "
            "'failed', 'cancelled', 'awaiting_goal_type')"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE goals ALTER COLUMN status TYPE goal_status "
            "USING status::text::goal_status"
        )
    )
    op.execute(sa.text("DROP TYPE goal_status_old"))

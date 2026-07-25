"""add reset_token_jtis for single-use password-reset enforcement

Revision ID: bf69a2bdec13
Revises: d5e6f7a8b9c0
Create Date: 2026-07-25 00:07:24.438599

Backfills the migration that commit 7d7d051 ("Secure password reset with
post-reset session revocation") never wrote. ``app/models/reset_token_jti.py``
landed with no Alembic revision, so on any Alembic-migrated database the table
simply did not exist — while ``app/routes/auth.py`` selects from it (:779) and
inserts into it (:806) on every password-reset confirm. The gap was invisible
because ``tests/conftest.py`` builds its schema with ``Base.metadata.create_all``,
which creates every mapped table regardless of migration history.

The unique index on ``jti`` is the actual replay defence — it is what makes a
reset token single-use — so it is created here as a UNIQUE index rather than a
plain one, matching ``unique=True, index=True`` on the model column.

NOTE ON AUTOGENERATE: `alembic revision --autogenerate` also emitted seven
``op.drop_index`` calls (ix_audit_events_event_type, ix_audit_events_goal_id,
ix_goals_user_id, ix_notifications_user_id, ix_payments_goal_id,
ix_payments_user_id, ix_proof_submissions_goal_id). Those are NOT stale
indexes: they were created deliberately by revision 7a8f3e2c1b4d
("add_user_id_indexes") and others, and autogenerate wants to drop them only
because the corresponding model columns never declared ``index=True`` — a
pre-existing model/migration drift. Dropping them would silently deoptimise
every per-user query, so they were removed from this revision by hand. The
drift is why ``alembic check`` still reports diffs after this migration; fixing
it means adding ``index=True`` to those model columns, which is out of scope
here.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "bf69a2bdec13"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the consumed-reset-token-JTI table."""
    op.create_table(
        "reset_token_jtis",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("jti", sa.String(length=36), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_reset_token_jtis_jti"),
        "reset_token_jtis",
        ["jti"],
        unique=True,
    )


def downgrade() -> None:
    """Drop the consumed-reset-token-JTI table."""
    op.drop_index(op.f("ix_reset_token_jtis_jti"), table_name="reset_token_jtis")
    op.drop_table("reset_token_jtis")

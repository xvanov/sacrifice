"""add user_id indexes

Revision ID: 7a8f3e2c1b4d
Revises: 4c2a1b8f9d3e
Create Date: 2026-05-24 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "7a8f3e2c1b4d"
down_revision: str | None = "4c2a1b8f9d3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_goals_user_id", "goals", ["user_id"])
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_goal_id", "payments", ["goal_id"])
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_proof_submissions_goal_id", "proof_submissions", ["goal_id"])


def downgrade() -> None:
    op.drop_index("ix_proof_submissions_goal_id", table_name="proof_submissions")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_index("ix_payments_goal_id", table_name="payments")
    op.drop_index("ix_payments_user_id", table_name="payments")
    op.drop_index("ix_goals_user_id", table_name="goals")

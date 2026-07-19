"""add github_repo goal type

Revision ID: 4c2a1b8f9d3e
Revises: e897b89aaf97
Create Date: 2026-05-23 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "4c2a1b8f9d3e"
down_revision: str | None = "e897b89aaf97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE goal_type ADD VALUE IF NOT EXISTS 'github_repo'")
    op.execute("ALTER TYPE criteria_type ADD VALUE IF NOT EXISTS 'github_repo'")


def downgrade() -> None:
    pass

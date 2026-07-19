"""update_chat_sessions_schema

Revision ID: b2d3e4f5a6c7
Revises: c80139861bbf
Create Date: 2026-06-11 15:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2d3e4f5a6c7"
down_revision: str | Sequence[str] | None = "c80139861bbf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """ADD the D010 generation-linkage columns to chat_sessions.

    Additive only: the D009 schema (messages/draft_goal/status) stays — the
    create-session endpoint depends on it. session_id stays nullable
    (sessions created via POST /api/chat/sessions have no external string id;
    the model treats it as optional)."""
    op.add_column("chat_sessions", sa.Column("session_id", sa.String(length=255), nullable=True))
    op.add_column("chat_sessions", sa.Column("goal_id", sa.UUID(), nullable=True))
    op.add_column(
        "chat_sessions", sa.Column("awaiting_direction_id", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "chat_sessions", sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        op.f("ix_chat_sessions_session_id"), "chat_sessions", ["session_id"], unique=True
    )
    op.create_foreign_key(None, "chat_sessions", "goals", ["goal_id"], ["id"])

    # Backfill for existing rows.
    op.execute("UPDATE chat_sessions SET session_id = id::text WHERE session_id IS NULL")
    op.execute("UPDATE chat_sessions SET last_activity_at = NOW() WHERE last_activity_at IS NULL")
    op.alter_column("chat_sessions", "last_activity_at", nullable=False)


def downgrade() -> None:
    """Drop the D010 generation-linkage columns only."""
    op.drop_constraint("chat_sessions_goal_id_fkey", "chat_sessions", type_="foreignkey")
    op.drop_index(op.f("ix_chat_sessions_session_id"), table_name="chat_sessions")
    op.drop_column("chat_sessions", "last_activity_at")
    op.drop_column("chat_sessions", "awaiting_direction_id")
    op.drop_column("chat_sessions", "goal_id")
    op.drop_column("chat_sessions", "session_id")

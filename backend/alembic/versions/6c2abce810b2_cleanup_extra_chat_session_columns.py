"""cleanup extra chat_session columns not in story schema

Revision ID: 6c2abce810b2
Revises: 74b288f75c85
Create Date: 2026-06-11 23:50:15.569165+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6c2abce810b2'
down_revision: Union[str, Sequence[str], None] = '74b288f75c85'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop columns that are not in the D009 story schema for chat_sessions."""
    # Drop FK constraint before column
    op.drop_constraint("chat_sessions_goal_id_fkey", "chat_sessions", type_="foreignkey")
    op.drop_index("ix_chat_sessions_session_id", table_name="chat_sessions")
    op.drop_column("chat_sessions", "session_id")
    op.drop_column("chat_sessions", "goal_id")
    op.drop_column("chat_sessions", "awaiting_direction_id")
    op.drop_column("chat_sessions", "last_activity_at")


def downgrade() -> None:
    """Restore the extra columns (reverse of upgrade)."""
    op.add_column(
        "chat_sessions",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("awaiting_direction_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("goal_id", sa.dialects.postgresql.UUID(), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("session_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_chat_sessions_session_id", "chat_sessions", ["session_id"], unique=True
    )
    op.create_foreign_key(
        "chat_sessions_goal_id_fkey",
        "chat_sessions",
        "goals",
        ["goal_id"],
        ["id"],
    )
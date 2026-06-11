"""update_chat_sessions_schema

Revision ID: b2d3e4f5a6c7
Revises: c80139861bbf
Create Date: 2026-06-11 15:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2d3e4f5a6c7'
down_revision: Union[str, Sequence[str], None] = 'c80139861bbf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Migrate chat_sessions from old schema (messages/draft_goal/status)
    to new schema (session_id/goal_id/awaiting_direction_id/last_activity_at)."""
    op.add_column('chat_sessions', sa.Column('session_id', sa.String(length=255), nullable=True))
    op.add_column('chat_sessions', sa.Column('goal_id', sa.UUID(), nullable=True))
    op.add_column('chat_sessions', sa.Column('awaiting_direction_id', sa.String(length=255), nullable=True))
    op.add_column('chat_sessions', sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_chat_sessions_session_id'), 'chat_sessions', ['session_id'], unique=True)
    op.create_foreign_key(None, 'chat_sessions', 'goals', ['goal_id'], ['id'])

    # Populate session_id from id for existing rows before making it NOT NULL
    op.execute("UPDATE chat_sessions SET session_id = id::text WHERE session_id IS NULL")
    op.execute("UPDATE chat_sessions SET last_activity_at = NOW() WHERE last_activity_at IS NULL")
    op.alter_column('chat_sessions', 'session_id', nullable=False)
    op.alter_column('chat_sessions', 'last_activity_at', nullable=False)

    # Drop old columns
    op.drop_column('chat_sessions', 'status')
    op.drop_column('chat_sessions', 'messages')
    op.drop_column('chat_sessions', 'draft_goal')


def downgrade() -> None:
    """Restore old chat_sessions schema."""
    op.add_column('chat_sessions', sa.Column('draft_goal', postgresql.JSONB(astext_type=sa.Text()),
                   server_default=sa.text("'{}'::jsonb"), autoincrement=False, nullable=True))
    op.add_column('chat_sessions', sa.Column('messages', postgresql.JSONB(astext_type=sa.Text()),
                   server_default=sa.text("'[]'::jsonb"), autoincrement=False, nullable=True))
    op.add_column('chat_sessions', sa.Column('status', postgresql.ENUM('active', 'goal_created', 'awaiting_goal_type', name='chat_session_status'),
                   server_default=sa.text("'active'::chat_session_status"), autoincrement=False, nullable=True))
    op.execute("UPDATE chat_sessions SET status = 'active' WHERE status IS NULL")
    op.execute("UPDATE chat_sessions SET messages = '[]'::jsonb WHERE messages IS NULL")
    op.execute("UPDATE chat_sessions SET draft_goal = '{}'::jsonb WHERE draft_goal IS NULL")
    op.alter_column('chat_sessions', 'status', nullable=False)
    op.alter_column('chat_sessions', 'messages', nullable=False)
    op.alter_column('chat_sessions', 'draft_goal', nullable=False)

    op.drop_constraint('chat_sessions_goal_id_fkey', 'chat_sessions', type_='foreignkey')
    op.drop_index(op.f('ix_chat_sessions_session_id'), table_name='chat_sessions')
    op.drop_column('chat_sessions', 'last_activity_at')
    op.drop_column('chat_sessions', 'awaiting_direction_id')
    op.drop_column('chat_sessions', 'goal_id')
    op.drop_column('chat_sessions', 'session_id')
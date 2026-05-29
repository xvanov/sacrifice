"""add chat_sessions columns for direction tracking

Revision ID: e0fe7edc0f4b
Revises: a40c6b79a019
Create Date: 2026-05-29 01:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e0fe7edc0f4b'
down_revision: Union[str, Sequence[str], None] = 'a40c6b79a019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create chat_sessions if not exists, then add direction_id, summary,
    created_at, updated_at."""
    conn = op.get_bind()

    # Ensure the chat_sessions table exists (it was created directly by
    # Base.metadata.create_all in some environments but never via a proper
    # Alembic migration in this chain).
    result = conn.execute(sa.text(
        "SELECT to_regclass('chat_sessions')"
    ))
    if result.scalar() is None:
        op.create_table('chat_sessions',
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('session_id', sa.String(255), nullable=False, unique=True, index=True),
            sa.Column('user_id', sa.UUID(), nullable=False),
            sa.Column('title', sa.String(255), nullable=True),
            sa.Column('direction_id', sa.String(255), nullable=True),
            sa.Column('summary', sa.String(1024), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True),
                       server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True),
                       server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        # All columns added via create_table — skip column checks below.
        return

    # direction_id
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='chat_sessions' AND column_name='direction_id'"
    ))
    if result.first() is None:
        op.add_column('chat_sessions',
            sa.Column('direction_id', sa.String(255), nullable=True))

    # summary
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='chat_sessions' AND column_name='summary'"
    ))
    if result.first() is None:
        op.add_column('chat_sessions',
            sa.Column('summary', sa.String(1024), nullable=True))

    # created_at
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='chat_sessions' AND column_name='created_at'"
    ))
    if result.first() is None:
        op.add_column('chat_sessions',
            sa.Column('created_at', sa.DateTime(timezone=True),
                       server_default=sa.text('now()'), nullable=False))

    # updated_at
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='chat_sessions' AND column_name='updated_at'"
    ))
    if result.first() is None:
        op.add_column('chat_sessions',
            sa.Column('updated_at', sa.DateTime(timezone=True),
                       server_default=sa.text('now()'), nullable=False))


def downgrade() -> None:
    """Remove added columns from chat_sessions."""
    for col in ('updated_at', 'created_at', 'summary', 'direction_id'):
        op.drop_column('chat_sessions', col)
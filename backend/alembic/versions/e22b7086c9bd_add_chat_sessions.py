"""add_chat_sessions

Revision ID: e22b7086c9bd
Revises: 9d4f2a6e1c70
Create Date: (auto-generated)

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e22b7086c9bd'
down_revision: Union[str, Sequence[str], None] = '9d4f2a6e1c70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Drop stale media_uploads table from prior development branch
    op.drop_table('media_uploads', if_exists=True)

    chat_session_status = sa.Enum(
        'active', 'goal_created', 'awaiting_goal_type',
        name='chat_session_status',
    )

    op.create_table(
        'chat_sessions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column(
            'messages',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column('draft_goal', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('status', chat_session_status, nullable=False, server_default='active'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('chat_sessions_user_id_fkey')),
        sa.PrimaryKeyConstraint('id', name=op.f('chat_sessions_pkey')),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('chat_sessions')
    op.create_table(
        'media_uploads',
        sa.Column('user_id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('goal_id', sa.UUID(), autoincrement=False, nullable=True),
        sa.Column('sha256', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column('size_bytes', sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column('duration_seconds', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=False),
        sa.Column('mime_type', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column('storage_path', sa.VARCHAR(length=1024), autoincrement=False, nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
        sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(['goal_id'], ['goals.id'], name=op.f('media_uploads_goal_id_fkey')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('media_uploads_user_id_fkey')),
        sa.PrimaryKeyConstraint('id', name=op.f('media_uploads_pkey')),
    )

"""add chat_sessions

Revision ID: 8a1b2c3d4e5f
Revises: 9d4f2a6e1c70
Create Date: 2025-05-26 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '8a1b2c3d4e5f'
down_revision: Union[str, Sequence[str], None] = '9d4f2a6e1c70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'chat_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('messages', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('draft_goal', postgresql.JSONB(), nullable=True),
        sa.Column(
            'status',
            sa.Enum('active', 'goal_created', 'awaiting_goal_type', name='chat_session_status'),
            nullable=False,
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('chat_sessions')
    op.execute('DROP TYPE IF EXISTS chat_session_status')
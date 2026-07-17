"""add auth_sessions table

Revision ID: 6f8a2b1c4d5e
Revises: 9d4f2a6e1c70
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '6f8a2b1c4d5e'
down_revision: Union[str, None] = '9d4f2a6e1c70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'auth_sessions',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('access_jti', sa.String(length=64), nullable=False),
        sa.Column('refresh_token_hash', sa.String(length=64), nullable=False),
        sa.Column('refresh_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('replaced_by_session_id', sa.UUID(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['replaced_by_session_id'], ['auth_sessions.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('access_jti'),
        sa.UniqueConstraint('refresh_token_hash'),
    )
    op.create_index(op.f('ix_auth_sessions_user_id'), 'auth_sessions', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_auth_sessions_user_id'), table_name='auth_sessions')
    op.drop_table('auth_sessions')

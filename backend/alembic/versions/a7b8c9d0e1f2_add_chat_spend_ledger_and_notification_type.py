"""add_chat_spend_ledger_and_notification_type

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-05-28 08:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # chat_spend_ledger table
    op.create_table(
        'chat_spend_ledger',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('call_timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('cost_millicents', sa.Integer(), nullable=False),
        sa.Column('call_description', sa.String(255), nullable=True),
    )

    # chat_sessions table
    op.create_table(
        'chat_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text('gen_random_uuid()')),
        sa.Column('session_id', sa.String(255), nullable=False, unique=True, index=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('goal_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('goals.id'), nullable=True),
        sa.Column('awaiting_direction_id', sa.String(255), nullable=True),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    # notification_type enum: add goal_type_ready
    op.execute("ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'goal_type_ready'")

    # goal_type enum: add __generated__ for generated goal types
    op.execute("ALTER TYPE goal_type ADD VALUE IF NOT EXISTS '__generated__'")

    # goal_status enum: add awaiting_goal_type lifecycle state
    op.execute("ALTER TYPE goal_status ADD VALUE IF NOT EXISTS 'awaiting_goal_type'")

    # goals: add awaiting_direction_id nullable column
    op.add_column('goals', sa.Column('awaiting_direction_id', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_table('chat_sessions')
    op.drop_table('chat_spend_ledger')
    # PostgreSQL does not support dropping enum values; leave the enum as-is
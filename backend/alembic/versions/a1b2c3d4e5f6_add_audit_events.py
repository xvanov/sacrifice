"""add_audit_events

Revision ID: a1b2c3d4e5f6
Revises: c4f9d3b8e1a2
Create Date: 2026-06-13 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'c4f9d3b8e1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'audit_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('goal_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            'event_type',
            sa.Enum('proof_accepted', 'proof_rejected', name='audit_event_type'),
            nullable=False,
        ),
        sa.Column(
            'details',
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(['goal_id'], ['goals.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_audit_events_goal_id', 'audit_events', ['goal_id'],
    )
    op.create_index(
        'ix_audit_events_event_type', 'audit_events', ['event_type'],
    )


def downgrade() -> None:
    op.drop_index('ix_audit_events_event_type')
    op.drop_index('ix_audit_events_goal_id')
    op.drop_table('audit_events')
    op.execute('DROP TYPE IF EXISTS audit_event_type')
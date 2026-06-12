"""add awaiting_direction_id to goals, goal_type_ready to notification_type

Revision ID: c7d8e9f0a1b2
Revises: a1b2c3d4e5f6
Create Date: 2026-06-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add awaiting_direction_id column to goals
    op.add_column(
        'goals',
        sa.Column('awaiting_direction_id', sa.String(length=255), nullable=True)
    )

    # Add missing goal_status enum values
    op.execute("ALTER TYPE goal_status ADD VALUE IF NOT EXISTS 'payment_failed'")
    op.execute("ALTER TYPE goal_status ADD VALUE IF NOT EXISTS 'awaiting_goal_type'")

    # Add goal_type_ready to notification_type enum
    op.execute("ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'goal_type_ready'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('goals', 'awaiting_direction_id')
    # Enum values cannot be removed in PostgreSQL, so downgrade is best-effort
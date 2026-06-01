"""add_awaiting_goal_type_status_and_direction_linkage

Revision ID: f1a2b3c4d5e6
Revises: 9d4f2a6e1c70
Create Date: 2026-05-28 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = '9d4f2a6e1c70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add awaiting_direction_id column to goals
    op.add_column('goals', sa.Column('awaiting_direction_id', sa.String(255), nullable=True))

    # Alter goal_status enum to add 'awaiting_goal_type'
    # Using ALTER TYPE ... ADD VALUE (safe inside a transaction on PostgreSQL)
    op.execute("ALTER TYPE goal_status ADD VALUE IF NOT EXISTS 'awaiting_goal_type'")

    # Alter goal_type enum to add '__generated__' for generated-goal placeholders
    op.execute("ALTER TYPE goal_type ADD VALUE IF NOT EXISTS '__generated__'")

    # Add 'generated' to criteria_type enum for generated-goal placeholders
    op.execute("ALTER TYPE criteria_type ADD VALUE IF NOT EXISTS 'generated'")


def downgrade() -> None:
    op.drop_column('goals', 'awaiting_direction_id')
    # Enum value removals are not supported by PostgreSQL; leave as-is
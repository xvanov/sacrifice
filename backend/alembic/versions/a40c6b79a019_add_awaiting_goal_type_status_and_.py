"""add awaiting_goal_type status and awaiting_direction_id column

Revision ID: a40c6b79a019
Revises: 9d4f2a6e1c70
Create Date: 2026-05-29 01:15:27.471698

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a40c6b79a019'
down_revision: Union[str, Sequence[str], None] = '9d4f2a6e1c70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE goal_status ADD VALUE IF NOT EXISTS 'awaiting_goal_type'")
    op.execute("ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'goal_type_ready'")
    # Column may already exist from prior run; add only if missing.
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='goals' AND column_name='awaiting_direction_id'"
    ))
    if result.first() is None:
        op.add_column(
            'goals',
            sa.Column('awaiting_direction_id', sa.String(255), nullable=True),
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('goals', 'awaiting_direction_id')

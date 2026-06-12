"""switch_goal_type_to_varchar

Revision ID: 9c9bfbf53f11
Revises: a7b8c9d0e1f2
Create Date: 2026-06-11 14:46:44.156130

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9c9bfbf53f11'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('goals', 'goal_type',
               existing_type=postgresql.ENUM('youtube_video', 'api_endpoint', 'dev_sandbox', 'github_repo', '__generated__', name='goal_type'),
               type_=sa.String(length=255),
               existing_nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('goals', 'goal_type',
               existing_type=sa.String(length=255),
               type_=postgresql.ENUM('youtube_video', 'api_endpoint', 'dev_sandbox', 'github_repo', '__generated__', name='goal_type'),
               existing_nullable=False,
               postgresql_using='goal_type::goal_type')

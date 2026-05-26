"""add media_uploads table

Revision ID: 145ff56800f2
Revises: 9d4f2a6e1c70
Create Date: 2026-05-25 20:32:29.528886

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '145ff56800f2'
down_revision: Union[str, Sequence[str], None] = '9d4f2a6e1c70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'media_uploads',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('goal_id', sa.UUID(), nullable=True),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('mime_type', sa.String(length=127), nullable=False),
        sa.Column('storage_path', sa.String(length=1024), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['goal_id'], ['goals.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
    )


def downgrade() -> None:
    op.drop_table('media_uploads')
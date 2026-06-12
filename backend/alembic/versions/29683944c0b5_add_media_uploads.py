"""add_media_uploads

Revision ID: 29683944c0b5
Revises: 9d4f2a6e1c70
Create Date: 2026-06-12 01:25:24.598346

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '29683944c0b5'
down_revision: Union[str, Sequence[str], None] = '9d4f2a6e1c70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'media_uploads',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('goal_id', sa.UUID(), nullable=True),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('duration_seconds', sa.Float(), nullable=False),
        sa.Column('mime_type', sa.String(length=64), nullable=False),
        sa.Column('storage_path', sa.String(length=1024), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['goal_id'], ['goals.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('media_uploads')

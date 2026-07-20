"""add_email_verification_columns

Revision ID: ffabb0a9d9d1
Revises: a1b2c3d4e5f6
Create Date: 2026-07-20 05:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ffabb0a9d9d1'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add email verification columns to users table."""
    op.add_column('users', sa.Column(
        'email_verified',
        sa.Boolean(),
        nullable=False,
        server_default=sa.text('true'),
    ))
    op.add_column('users', sa.Column(
        'email_verification_jti',
        sa.String(36),
        nullable=True,
    ))


def downgrade() -> None:
    """Remove email verification columns from users table."""
    op.drop_column('users', 'email_verification_jti')
    op.drop_column('users', 'email_verified')
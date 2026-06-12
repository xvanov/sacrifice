"""add _generated criteria_type

Revision ID: 46d01656ccc2
Revises: 9d4f2a6e1c70
Create Date: 2026-06-12 03:05:04.107955

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '46d01656ccc2'
down_revision: Union[str, Sequence[str], None] = '9d4f2a6e1c70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE criteria_type ADD VALUE IF NOT EXISTS '_generated'")


def downgrade() -> None:
    """Downgrade schema."""
    pass

"""merge media_uploads and chat_sessions branches

Revision ID: 9b9c8f738404
Revises: 13ac1742b6ea, e22b7086c9bd
Create Date: 2026-06-11 15:50:27.864725

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b9c8f738404'
down_revision: Union[str, Sequence[str], None] = ('13ac1742b6ea', 'e22b7086c9bd')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

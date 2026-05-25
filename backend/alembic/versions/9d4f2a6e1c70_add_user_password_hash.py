"""add user password_hash column

Revision ID: 9d4f2a6e1c70
Revises: 7a8f3e2c1b4d
Create Date: 2026-05-24 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '9d4f2a6e1c70'
down_revision: Union[str, None] = '7a8f3e2c1b4d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_hash", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "password_hash")

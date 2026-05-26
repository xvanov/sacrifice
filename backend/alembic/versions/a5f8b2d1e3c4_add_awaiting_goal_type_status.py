"""add awaiting_goal_type status to goal_status enum

Revision ID: a5f8b2d1e3c4
Revises: 9d4f2a6e1c70
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a5f8b2d1e3c4'
down_revision: Union[str, None] = '9d4f2a6e1c70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE goal_status ADD VALUE IF NOT EXISTS 'awaiting_goal_type'")


def downgrade() -> None:
    pass
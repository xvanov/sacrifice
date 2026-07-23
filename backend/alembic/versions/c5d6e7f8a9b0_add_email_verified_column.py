"""add_email_verified_column

Revision ID: c5d6e7f8a9b0
Revises: b3c4d5e6f7a8
Create Date: 2026-07-19 00:00:01.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: only add if not present (the column may already exist).
    op.execute(
        sa.text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
            "email_verified BOOLEAN NOT NULL DEFAULT false"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE users DROP COLUMN IF EXISTS email_verified"))

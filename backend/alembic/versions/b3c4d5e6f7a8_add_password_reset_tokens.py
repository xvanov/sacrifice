"""add_password_reset_tokens

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-07-19 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: the table may already exist when create_all is also in use.
    op.execute(
        sa.text(
            """CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id UUID NOT NULL,
                user_id UUID NOT NULL,
                token_hash VARCHAR(128) NOT NULL,
                expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                consumed BOOLEAN NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
                UNIQUE (token_hash)
            )"""
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS password_reset_tokens"))

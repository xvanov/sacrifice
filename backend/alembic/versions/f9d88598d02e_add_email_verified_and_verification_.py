"""add email_verified and verification_tokens

Revision ID: f9d88598d02e
Revises: 7fc643119c48
Create Date: 2026-08-09 00:43:43.817813

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9d88598d02e"
down_revision: Union[str, Sequence[str], None] = "7fc643119c48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add email_verified column to users (IF NOT EXISTS for idempotency).
    op.execute(
        sa.text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT false"
        )
    )
    # OAuth accounts (google, github) are pre-verified.
    op.execute(
        sa.text(
            "UPDATE users SET email_verified = true "
            "WHERE auth_provider IN ('google', 'github')"
        )
    )

    # verification_tokens table.
    op.create_table(
        "verification_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_verification_tokens_token_hash"),
        "verification_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_verification_tokens_user_id"),
        "verification_tokens",
        ["user_id"],
        unique=False,
    )

    # Drop the old verified_email_tokens table if it exists (prior worktree cruft).
    op.execute(sa.text("DROP TABLE IF EXISTS verified_email_tokens CASCADE"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_verification_tokens_user_id"), table_name="verification_tokens"
    )
    op.drop_index(
        op.f("ix_verification_tokens_token_hash"), table_name="verification_tokens"
    )
    op.drop_table("verification_tokens")
    op.execute(sa.text("ALTER TABLE users DROP COLUMN IF EXISTS email_verified"))

"""add_email_verification_fields

Revision ID: d5e6f7a8b9c0
Revises: a1b2c3d4e5f6
Create Date: 2026-07-18 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=True))
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Backfill: OAuth-linked accounts (non-email provider) are already
    # verified by the provider; email/password accounts start unverified.
    op.execute(
        sa.text("UPDATE users SET email_verified = TRUE WHERE auth_provider != 'email'")
    )
    op.execute(
        sa.text("UPDATE users SET email_verified = FALSE WHERE auth_provider = 'email'")
    )

    op.alter_column("users", "email_verified", nullable=False)

    op.create_table(
        "verification_tokens",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )


def downgrade() -> None:
    op.drop_table("verification_tokens")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "email_verified")

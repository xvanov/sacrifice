"""add_email_verified_and_verification_tokens

Revision ID: 074067e27170
Revises: 7fc643119c48
Create Date: 2026-08-09 01:47:34.080545

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '074067e27170'
down_revision: Union[str, Sequence[str], None] = '7fc643119c48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_table(
        "verification_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("verification_tokens_user_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("verification_tokens_pkey")),
    )
    op.create_index(
        op.f("ix_verification_tokens_user_id"),
        "verification_tokens",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_verification_tokens_token_hash"),
        "verification_tokens",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_verification_tokens_token_hash"), table_name="verification_tokens")
    op.drop_index(op.f("ix_verification_tokens_user_id"), table_name="verification_tokens")
    op.drop_table("verification_tokens")
    op.drop_column("users", "email_verified")

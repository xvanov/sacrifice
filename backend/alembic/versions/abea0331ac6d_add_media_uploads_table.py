"""add media_uploads table

Revision ID: abea0331ac6d
Revises: 9d4f2a6e1c70
Create Date: 2026-05-28 06:42:21.720389

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "abea0331ac6d"
down_revision: Union[str, Sequence[str], None] = "9d4f2a6e1c70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_uploads",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "goal_id",
            postgresql.UUID(),
            sa.ForeignKey("goals.id"),
            nullable=True,
        ),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("mime_type", sa.String(50), nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("media_uploads")
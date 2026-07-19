"""add_user_auth_session_fields

Revision ID: c4f9d3b8e1a2
Revises: b2d3e4f5a6c7
Create Date: 2026-07-18 00:00:00.000000

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f9d3b8e1a2"
down_revision: str | Sequence[str] | None = "b2d3e4f5a6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("auth_session_id", sa.String(length=36), nullable=True))
    op.add_column("users", sa.Column("pending_auth_code_id", sa.String(length=36), nullable=True))

    bind = op.get_bind()
    metadata = sa.MetaData()
    users = sa.Table("users", metadata, autoload_with=bind)
    existing_user_ids = bind.execute(sa.select(users.c.id)).scalars().all()
    for user_id in existing_user_ids:
        bind.execute(
            users.update().where(users.c.id == user_id).values(auth_session_id=str(uuid.uuid4()))
        )

    op.alter_column(
        "users",
        "auth_session_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("users", "pending_auth_code_id")
    op.drop_column("users", "auth_session_id")

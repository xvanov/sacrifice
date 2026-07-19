"""switch_criteria_type_to_varchar

Revision ID: c80139861bbf
Revises: 9c9bfbf53f11
Create Date: 2026-06-11 15:06:51.973317

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c80139861bbf"
down_revision: str | Sequence[str] | None = "9c9bfbf53f11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Switch criteria_type from PG enum to VARCHAR so dynamic goal types can be stored."""
    op.alter_column(
        "goal_criteria",
        "criteria_type",
        existing_type=postgresql.ENUM(
            "youtube",
            "api_endpoint",
            "dev_sandbox",
            "github_repo",
            "generated",
            name="criteria_type",
        ),
        type_=sa.String(length=255),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Restore criteria_type PG enum."""
    op.alter_column(
        "goal_criteria",
        "criteria_type",
        existing_type=sa.String(length=255),
        type_=postgresql.ENUM(
            "youtube",
            "api_endpoint",
            "dev_sandbox",
            "github_repo",
            "generated",
            name="criteria_type",
        ),
        existing_nullable=False,
        postgresql_using="criteria_type::criteria_type",
    )

"""add proof_dispatch_failed to audit_event_type

A validated proof that could not be handed to the verification queue (broker
down) is neither accepted-and-queued nor rejected. It gets its own audit value
so an operator can find these without reading worker logs, and so the existing
"exactly one proof_rejected/proof_accepted event" invariants keep holding.

Postgres has no ``DROP VALUE``, so the downgrade recreates the type without the
value. That requires removing rows that use it: they are diagnostic records of a
transient infrastructure failure, not user data, and the recoverable state lives
on ``proof_submissions`` (dispatch_attempts / dispatched_at) either way.

``ALTER TYPE ... ADD VALUE`` runs inside Alembic's transaction safely on
PostgreSQL 12+ as long as the new value is not *used* in the same transaction,
which it is not here.

Revision ID: e7a8b9c0d1e2
Revises: bf69a2bdec13
Create Date: 2026-07-25 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e7a8b9c0d1e2"
down_revision: Union[str, None] = "bf69a2bdec13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_VALUE = "proof_dispatch_failed"
_ORIGINAL_VALUES = ("proof_accepted", "proof_rejected")


def upgrade() -> None:
    op.execute(
        sa.text(f"ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS '{_NEW_VALUE}'")
    )


def downgrade() -> None:
    # Rows carrying the value cannot survive a type that lacks it.
    op.execute(sa.text(f"DELETE FROM audit_events WHERE event_type = '{_NEW_VALUE}'"))
    values = ", ".join(f"'{v}'" for v in _ORIGINAL_VALUES)
    op.execute(sa.text("ALTER TYPE audit_event_type RENAME TO audit_event_type_old"))
    op.execute(sa.text(f"CREATE TYPE audit_event_type AS ENUM ({values})"))
    op.execute(
        sa.text(
            "ALTER TABLE audit_events "
            "ALTER COLUMN event_type TYPE audit_event_type "
            "USING event_type::text::audit_event_type"
        )
    )
    op.execute(sa.text("DROP TYPE audit_event_type_old"))

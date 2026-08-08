"""add goals.charge_after: a deferred-charge buffer until local midnight

A goal's pledge used to be charged the instant it resolved to `failed` — right
at the deadline moment, which can be any time of day. This adds a buffer: the
goal still resolves to `failed` immediately, but the actual Stripe charge is
deferred until midnight (in the goal's own `timezone`) on the day it failed.
`app/workers/payments.py`'s new `process_deferred_charges` sweep is what
actually fires the charge once `charge_after` has passed.

Nullable, no backfill on existing rows — that is the point, not an oversight.
A goal that resolved to `failed` (charged or not) before this column existed
must never retroactively acquire a `charge_after` and get swept into the new
deferred-charge mechanism: the sweep's query is `charge_after IS NOT NULL AND
charge_after <= now()`, so a row that was never given a value is invisible to
it. Only the (updated) resolution code paths ever write this column, and only
going forward.

Revision ID: 7fc643119c48
Revises: b8e4c1a70d92
Create Date: 2026-07-31 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7fc643119c48"
down_revision: Union[str, None] = "b8e4c1a70d92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "goals",
        sa.Column("charge_after", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("goals", "charge_after")

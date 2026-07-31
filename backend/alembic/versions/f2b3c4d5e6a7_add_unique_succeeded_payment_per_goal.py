"""add partial unique index: at most one succeeded payment per goal

A verified-as-failed goal collects the pledge via ``process_charge_for_goal``,
which creates a real off-session PaymentIntent. Its double-charge guard is a
plain read-then-write (``SELECT id, status FROM payments WHERE goal_id = ...``,
skip if a row exists) with no uniqueness behind it, so two concurrent attempts —
two overlapping deadline sweeps, or a re-dispatched verification — can both read
"no row" and both insert. Stripe's ``idempotency_key=f"goal-charge-{goal_id}"``
collapses the *money* into a single PaymentIntent, but Stripe idempotency keys
expire after 24 hours: two attempts more than a day apart are not deduped by
Stripe, and only a committed payments row protects the charge. The ledger can
also double-count even when the money does not.

The index is PARTIAL — ``UNIQUE (goal_id) WHERE status = 'succeeded'`` — not a
plain ``UNIQUE (goal_id)``. Non-succeeded payment rows legitimately exist for a
goal that is still chargeable in principle: the no-payment-method path and the
retry-exhausted path both insert ``status='failed'``, and the Stripe webhook
reconciler promotes a row to 'succeeded' with an ``UPDATE``. Postgres evaluates
a partial unique index on UPDATE as well as INSERT, so a row entering the
predicate is checked too. A plain unique constraint would cement "one charge
attempt ever per goal" into the schema and make the eventual fix require both
another migration and data repair.

Populated-table safety: this refuses to run if any goal already has more than
one succeeded payment, and reports the offending goal_ids instead of letting
``CREATE UNIQUE INDEX`` fail with an opaque error. Duplicates are NOT repaired
automatically — a duplicate succeeded row means real money may have moved twice,
which needs a human to reconcile against Stripe and refund, not a migration
silently deleting financial records.

Revision ID: f2b3c4d5e6a7
Revises: e7a8b9c0d1e2
Create Date: 2026-07-25 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f2b3c4d5e6a7"
down_revision: Union[str, None] = "e7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = "uq_payments_goal_id_succeeded"
_PREDICATE = "status = 'succeeded'"


def upgrade() -> None:
    conn = op.get_bind()

    # Pre-flight: a pre-existing duplicate would make CREATE UNIQUE INDEX fail
    # mid-migration with only the first offending value named. Surface all of
    # them up front, and refuse rather than mutating the ledger.
    duplicates = conn.execute(
        sa.text(
            f"""
            SELECT goal_id, count(*) AS n
            FROM payments
            WHERE {_PREDICATE}
            GROUP BY goal_id
            HAVING count(*) > 1
            ORDER BY goal_id
            """
        )
    ).all()
    if duplicates:
        detail = ", ".join(f"{row.goal_id} ({row.n} rows)" for row in duplicates)
        raise RuntimeError(
            f"Cannot create {_INDEX_NAME}: these goals already have more than "
            f"one succeeded payment — {detail}. Each is a possible "
            "double-collection: reconcile the PaymentIntents against Stripe, "
            "refund any genuine duplicate charge, and reduce each goal to a "
            "single succeeded payments row before re-running this migration. "
            "Refusing to delete payment records automatically."
        )

    op.create_index(
        _INDEX_NAME,
        "payments",
        ["goal_id"],
        unique=True,
        postgresql_where=sa.text(_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="payments")

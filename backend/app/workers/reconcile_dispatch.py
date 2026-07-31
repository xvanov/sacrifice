"""Re-dispatch verification for proofs whose task never reached the worker.

Why this exists
---------------
``POST /api/goals/{id}/submit-proof`` persists the submission and then hands the
verification to Celery. That hand-off deliberately never fails the request — a
broker outage must not discard a validated submission (see
``app/routes/goals.py``, ``_persist_and_dispatch_proof``). Without a sweep, the
cost of that choice lands on the user: the submission stays ``pending`` forever
while ``check_deadlines`` sees the goal still ``active`` past its deadline and
charges the pledge. The user submitted valid proof, was told it was received,
and gets billed as though they never submitted.

A time-based sweep is used rather than only reacting to a recorded dispatch
failure because three distinct faults are indistinguishable from the row:

1. the enqueue itself failed (broker down) — ``dispatched_at IS NULL``;
2. the task was enqueued and acked, then the worker died mid-verification;
3. the task was lost in flight (broker eviction, queue purge).

Only elapsed time catches all three.

Not double-charging
-------------------
Re-running a verification that actually completed would be dangerous:
``persist_verification_result`` charges the pledge when the result is
``failed``. Four things keep that from happening — see the module tests:

* Candidates must still be ``verification_status = 'pending'``. Any completed
  verification (verified OR failed) has already moved the row, so it can never
  be selected.
* Candidates must be older than ``verification_dispatch_stale_minutes``, which
  is configured to exceed the worst-case in-flight verification, so a running
  verification is not duplicated.
* Rows are claimed with ``FOR UPDATE SKIP LOCKED`` and the attempt counter is
  incremented in the same statement, so two overlapping beat ticks cannot claim
  the same submission.
* Even if a duplicate verification did land on ``failed``, the charge is
  idempotent below this layer: ``process_charge_for_goal`` returns early when a
  payment row already exists for the goal, and its Stripe
  ``PaymentIntent.create`` call passes ``idempotency_key=f"goal-charge-{id}"``.

The claim is written BEFORE the dispatch on purpose. If the process dies in
between, the attempt is recorded but the task was never queued: the row waits
one more window and is retried, bounded by the attempt cap. The opposite order
could dispatch without recording, which is the only failure mode that can
duplicate work.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.celery_app import celery_app
from app.goal_types import registry as goal_type_registry

logger = logging.getLogger(__name__)


def _get_session():
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    return engine, session_factory


# One statement: pick eligible rows, lock them against concurrent ticks, and
# record the attempt. RETURNING gives us exactly the rows this tick owns.
#
# ``submitted_at < :cutoff`` (not just ``dispatched_at``) is what makes the
# in-flight submit-proof request safe: between the request's INSERT commit and
# its dispatch bookkeeping commit the row briefly looks un-dispatched, and
# without this predicate a beat tick landing in that window would double-queue
# a proof that was about to be dispatched normally.
_CLAIM_SQL = text(
    """
    UPDATE proof_submissions AS ps
    SET dispatch_attempts = ps.dispatch_attempts + 1,
        dispatched_at = :now
    WHERE ps.id IN (
        SELECT c.id
        FROM proof_submissions AS c
        WHERE c.verification_status = 'pending'
          AND c.dispatch_attempts < :max_attempts
          AND c.submitted_at < :cutoff
          AND (c.dispatched_at IS NULL OR c.dispatched_at < :cutoff)
        ORDER BY c.submitted_at
        FOR UPDATE SKIP LOCKED
        LIMIT :batch_size
    )
    RETURNING ps.id, ps.goal_id, ps.proof_data, ps.dispatch_criteria,
              ps.dispatch_attempts
    """
)


# Once a verdict is in, the criteria snapshot has no remaining purpose, and for
# github_repo goals it holds an encrypted PAT. Holding a token-bearing blob
# forever is standing risk for no benefit, so the sweep clears it.
#
# Deliberately restricted to terminal verdicts: an INCONCLUSIVE outcome puts the
# row back to 'pending' precisely so it can be re-dispatched
# (app/services/verification_result.py), and that replay needs the snapshot.
_CLEAR_RESOLVED_CRITERIA_SQL = text(
    """
    UPDATE proof_submissions
    SET dispatch_criteria = NULL
    WHERE verification_status IN ('verified', 'failed')
      AND dispatch_criteria IS NOT NULL
    """
)


async def clear_resolved_dispatch_criteria(db: AsyncSession) -> int:
    """Drop the criteria snapshot from submissions that reached a verdict.

    Returns the number of rows cleared. Bounds how long an encrypted token is
    retained to one beat interval past the verdict.
    """
    result = await db.execute(_CLEAR_RESOLVED_CRITERIA_SQL)
    await db.commit()
    return result.rowcount or 0


async def reconcile_stale_dispatches(db: AsyncSession | None = None) -> dict:
    """Re-dispatch verification for stale ``pending`` submissions.

    Returns a summary dict: how many rows were claimed, re-dispatched, and how
    many failed to re-dispatch (broker still unavailable).
    """
    engine = None
    if db is None:
        engine, session_factory = _get_session()
        session = session_factory()
    else:
        session = db

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=settings.verification_dispatch_stale_minutes)

    try:
        result = await session.execute(
            _CLAIM_SQL,
            {
                "now": now,
                "cutoff": cutoff,
                "max_attempts": settings.verification_dispatch_max_attempts,
                "batch_size": settings.verification_dispatch_batch_size,
            },
        )
        claimed = result.fetchall()
        # Commit the claim before dispatching: the attempt must be durable even
        # if this process dies mid-loop.
        await session.commit()

        redispatched = 0
        failed = 0
        skipped_unknown_type = 0

        for row in claimed:
            goal_row = await session.execute(
                text("SELECT goal_type FROM goals WHERE id = :goal_id"),
                {"goal_id": row.goal_id},
            )
            goal_type_name = goal_row.scalar_one_or_none()
            if goal_type_name is None:
                skipped_unknown_type += 1
                continue

            try:
                goal_type = goal_type_registry.get_type(goal_type_name)
            except KeyError:
                # The goal type was removed or renamed since submission. Nothing
                # to re-dispatch to; leave the row for an operator.
                logger.warning(
                    "Cannot re-dispatch submission %s: unknown goal type %r",
                    row.id,
                    goal_type_name,
                )
                skipped_unknown_type += 1
                continue

            dispatch = getattr(goal_type, "dispatch_verification", None)
            if not callable(dispatch):
                skipped_unknown_type += 1
                continue

            try:
                dispatch(
                    goal_id=str(row.goal_id),
                    submission_id=str(row.id),
                    proof_data=row.proof_data or {},
                    # Replay of the original call. Falls back to {} rather than
                    # re-deriving from goal_criteria: a re-derived value can
                    # differ from what was originally verified (a missing
                    # github_token turns a passing private-repo proof into a
                    # failure, and a failure charges the pledge).
                    criteria_data=row.dispatch_criteria or {},
                )
                redispatched += 1
                logger.info(
                    "Re-dispatched verification for submission %s (attempt %d)",
                    row.id,
                    row.dispatch_attempts,
                )
            except Exception:
                # Broker still down. The attempt is already recorded, so this
                # row backs off by one staleness window and stops entirely once
                # the cap is reached.
                failed += 1
                logger.warning(
                    "Re-dispatch failed for submission %s (attempt %d)",
                    row.id,
                    row.dispatch_attempts,
                )

        # Housekeeping on the same tick: retire criteria snapshots (and the
        # encrypted tokens in them) for submissions that already have a verdict.
        cleared = await clear_resolved_dispatch_criteria(session)

        return {
            "claimed": len(claimed),
            "redispatched": redispatched,
            "failed": failed,
            "skipped_unknown_type": skipped_unknown_type,
            "criteria_cleared": cleared,
            "cutoff": cutoff.isoformat(),
        }
    finally:
        if engine is not None:
            await session.close()
            await engine.dispose()


async def count_stale_dispatches(db: AsyncSession) -> int:
    """Number of submissions the next sweep would claim. For tests/operators."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=settings.verification_dispatch_stale_minutes
    )
    result = await db.execute(
        text(
            """
            SELECT COUNT(*) FROM proof_submissions
            WHERE verification_status = 'pending'
              AND dispatch_attempts < :max_attempts
              AND submitted_at < :cutoff
              AND (dispatched_at IS NULL OR dispatched_at < :cutoff)
            """
        ),
        {
            "cutoff": cutoff,
            "max_attempts": settings.verification_dispatch_max_attempts,
        },
    )
    return int(result.scalar_one())


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def reconcile_dispatch_task(self):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(reconcile_stale_dispatches())
    finally:
        loop.close()

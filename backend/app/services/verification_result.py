"""Shared persistence for verification-worker results.

Every goal-type worker previously carried an identical copy of
``_persist_result`` that updated the submission + goal and notified the user —
but none of them dispatched the pledge charge. A goal failed by verification
is terminal (submit-proof requires status ``active``) and the deadline sweep
only enforces ``active``/``pending_review`` goals, so the charge promised by
the failure notification never happened. Charging now lives here, on the same
path that marks the goal failed.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.goal import Goal
from app.models.proof import ProofSubmission
from app.services.notification import notify_goal_resolution

logger = logging.getLogger(__name__)


async def persist_verification_result(
    db: AsyncSession,
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    status: str,
    details: dict,
) -> None:
    result = await db.execute(
        select(ProofSubmission).where(ProofSubmission.id == submission_id)
    )
    submission = result.scalar_one_or_none()
    if submission:
        submission.verification_status = status
        submission.verification_details = details

    result = await db.execute(select(Goal).where(Goal.id == goal_id))
    goal = result.scalar_one_or_none()
    if goal:
        goal.status = status
        # Notify the user their goal was resolved (verified/failed).
        await notify_goal_resolution(db, goal, status)

    await db.commit()

    if goal is not None and status == "failed":
        # Imported lazily: workers import this module, and payments imports
        # celery_app — keep the import cycle out of module import time.
        from app.workers.payments import process_charge_for_goal

        try:
            await process_charge_for_goal(str(goal.id), str(goal.user_id))
        except Exception:
            # The charge records its own failure state; never let a billing
            # error mask the verification result that was already committed.
            logger.exception("Charge processing failed for goal %s", goal.id)

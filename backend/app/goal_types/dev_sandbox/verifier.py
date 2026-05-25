"""Verifier for dev_sandbox goal type.

Reuses the existing verification logic from app.workers.dev_sandbox.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.workers.dev_sandbox import run_dev_sandbox_verification


async def verify(proof_data: dict, criteria_data: dict) -> dict:
    """Run dev sandbox verification.

    Note: This is a simplified wrapper — the full verification flow
    (including DB persistence) is managed by the Celery task in
    app.workers.dev_sandbox.  This verifier is used by the registry
    dispatch to initiate or run inline verification.

    Returns:
        {"verification_status": "verified"|"failed", "verification_details": {...}}
    """
    # The verifier calls the existing worker's core function with dummy IDs
    # for inline verification (non-persisted path).
    result = await run_dev_sandbox_verification(
        goal_id=uuid.uuid4(),
        submission_id=uuid.uuid4(),
        proof_data=proof_data,
        criteria_data=criteria_data,
        db=None,
    )
    return result
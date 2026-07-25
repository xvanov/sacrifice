"""Verifier for github_repo goal type.

Reuses the existing verification logic from app.workers.github_repo.
"""

from app.core.verification_guard import run_with_verification_guard
from app.workers.github_repo import verify_github_repo


async def verify(proof_data: dict, criteria_data: dict) -> dict:
    """Run GitHub repo verification with timeout and concurrency guard.

    Returns:
        ``{"verification_status": "verified"|"failed"|"inconclusive",
        "verification_details": {...}, "inconclusive_reason": str | None}``

    ``inconclusive`` means the check could not be completed (GitHub outage, rate
    limit, criteria we cannot evaluate) and must never be treated as a verdict —
    it is the outcome that does not charge the pledge. Any caller that persists
    this result has to forward ``inconclusive_reason`` to
    ``services.verification_result.persist_verification_result``, which rejects
    the outcome without it.
    """
    return await run_with_verification_guard(
        verify_github_repo,
        proof_data,
        criteria_data,
    )

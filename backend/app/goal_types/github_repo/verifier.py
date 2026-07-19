"""Verifier for github_repo goal type.

Reuses the existing verification logic from app.workers.github_repo.
"""

from app.core.verification_guard import run_with_verification_guard
from app.workers.github_repo import verify_github_repo


async def verify(proof_data: dict, criteria_data: dict) -> dict:
    """Run GitHub repo verification with timeout and concurrency guard.

    Returns:
        {"verification_status": "verified"|"failed", "verification_details": {...}}
    """
    return await run_with_verification_guard(
        verify_github_repo,
        proof_data,
        criteria_data,
    )

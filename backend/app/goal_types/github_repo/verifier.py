"""Verifier for github_repo goal type.

Reuses the existing verification logic from app.workers.github_repo.
"""

from app.workers.github_repo import verify_github_repo


async def verify(proof_data: dict, criteria_data: dict) -> dict:
    """Run GitHub repo verification.

    Returns:
        {"verification_status": "verified"|"failed", "verification_details": {...}}
    """
    return await verify_github_repo(proof_data, criteria_data)
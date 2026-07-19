"""Verifier for youtube_video goal type.

Reuses the existing verification logic from app.workers.youtube.
"""

from app.core.verification_guard import run_with_verification_guard
from app.workers.youtube import verify_youtube_content


async def verify(proof_data: dict, criteria_data: dict) -> dict:
    """Run YouTube content verification with timeout and concurrency guard.

    Returns:
        {"verification_status": "verified"|"failed", "verification_details": {...}}
    """
    return await run_with_verification_guard(
        verify_youtube_content,
        proof_data,
        criteria_data,
    )

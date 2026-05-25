"""Verifier for youtube_video goal type.

Reuses the existing verification logic from app.workers.youtube.
"""

from app.workers.youtube import verify_youtube_content


async def verify(proof_data: dict, criteria_data: dict) -> dict:
    """Run YouTube content verification.

    Returns:
        {"verification_status": "verified"|"failed", "verification_details": {...}}
    """
    return await verify_youtube_content(proof_data, criteria_data)
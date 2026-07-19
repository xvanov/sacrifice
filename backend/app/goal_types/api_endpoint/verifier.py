"""Verifier for api_endpoint goal type.

Reuses the existing verification logic from app.workers.api_check.
"""

from app.core.verification_guard import run_with_verification_guard
from app.workers.api_check import verify_api_endpoint


async def verify(proof_data: dict, criteria_data: dict) -> dict:
    """Run API endpoint verification with timeout and concurrency guard.

    Returns:
        {"verification_status": "verified"|"failed", "verification_details": {...}}
    """
    return await run_with_verification_guard(
        verify_api_endpoint,
        proof_data,
        criteria_data,
    )

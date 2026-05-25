"""Verifier for api_endpoint goal type.

Reuses the existing verification logic from app.workers.api_check.
"""

from app.workers.api_check import verify_api_endpoint


async def verify(proof_data: dict, criteria_data: dict) -> dict:
    """Run API endpoint verification.

    Returns:
        {"verification_status": "verified"|"failed", "verification_details": {...}}
    """
    return await verify_api_endpoint(proof_data, criteria_data)
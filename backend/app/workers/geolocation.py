"""Geolocation goal verification.

Verifies that submitted GPS coordinates fall within ``radius_m`` metres of
the goal's target location. The time dimension is enforced by the submission
pipeline itself: proof can only be submitted while the goal is active (the
deadline worker fails it afterwards), and the server records the submission
timestamp — so "be at X by time T" means "submit proof from X before T".
"""

import asyncio
import math
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.database import async_session
from app.services.verification_result import persist_verification_result

# 500ft, converted to metres — GPS accuracy in normal outdoor conditions plus
# how imprecise "the exact spot on the map" is versus where a person is
# actually standing make a tighter default too easy to miss by a few metres.
DEFAULT_RADIUS_M = 152.4

_EARTH_RADIUS_M = 6_371_000


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


async def verify_geolocation(proof_data: dict, criteria_data: dict) -> dict:
    details: dict = {}
    try:
        lat = float(proof_data["latitude"])
        lon = float(proof_data["longitude"])
        target_lat = float(criteria_data["target_latitude"])
        target_lon = float(criteria_data["target_longitude"])
    except (KeyError, TypeError, ValueError) as e:
        details["error"] = f"Missing or invalid coordinates: {e}"
        details["location_passed"] = False
        return {"verification_status": "failed", "verification_details": details}

    radius_m = criteria_data.get("radius_m") or DEFAULT_RADIUS_M
    accuracy_m = proof_data.get("accuracy_m")

    distance_m = haversine_m(lat, lon, target_lat, target_lon)
    # A reading with e.g. 30m accuracy that lands 160m out on a 150m radius
    # may genuinely be inside; give the user the benefit of the reported GPS
    # accuracy (capped so a wildly imprecise reading can't buy a free pass).
    allowance_m = min(float(accuracy_m or 0), radius_m)
    within = distance_m <= radius_m + allowance_m

    details.update(
        {
            "submitted_latitude": lat,
            "submitted_longitude": lon,
            "target_latitude": target_lat,
            "target_longitude": target_lon,
            "distance_m": round(distance_m, 1),
            "radius_m": radius_m,
            "accuracy_m": accuracy_m,
            "location_passed": within,
        }
    )
    if not within:
        details["failure_reason"] = (
            f"Location is {distance_m:,.0f}m from the target — outside the "
            f"allowed {radius_m}m radius."
        )
    return {
        "verification_status": "verified" if within else "failed",
        "verification_details": details,
    }


async def run_geolocation_verification(
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    proof_data: dict,
    criteria_data: dict,
    db: AsyncSession | None = None,
) -> dict:
    result = await verify_geolocation(proof_data, criteria_data)

    if db is not None:
        await persist_verification_result(
            db, goal_id, submission_id,
            result["verification_status"], result["verification_details"],
        )
    else:
        async with async_session() as session:
            await persist_verification_result(
                session, goal_id, submission_id,
                result["verification_status"], result["verification_details"],
            )

    return result


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def run_geolocation_verification_task(
    self,
    goal_id_str: str,
    submission_id_str: str,
    proof_data: dict,
    criteria_data: dict,
):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            run_geolocation_verification(
                goal_id=uuid.UUID(goal_id_str),
                submission_id=uuid.UUID(submission_id_str),
                proof_data=proof_data,
                criteria_data=criteria_data,
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        loop.close()

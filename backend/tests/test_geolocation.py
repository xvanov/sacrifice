"""Tests for the geolocation goal type: verifier math, proof validation,
and the submit-proof route contract."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from app.workers.geolocation import haversine_m, verify_geolocation

from tests.test_api_endpoint_verification import _auth, make_client

pytestmark = pytest.mark.asyncio

# Golden Gate Bridge midpoint vs ~111m north of it.
GG_LAT, GG_LON = 37.8199, -122.4783


async def _create_active_geolocation_goal(client, token, radius_m=150):
    deadline = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Be at the bridge",
            "deadline": deadline,
            "pledge_amount": 500,
            "goal_type": "geolocation",
            "criteria": {
                "target_latitude": GG_LAT,
                "target_longitude": GG_LON,
                "radius_m": radius_m,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    goal_id = resp.json()["id"]
    resp = await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )
    assert resp.status_code == 200, resp.text
    return goal_id


# ─── verifier unit tests ───


def test_haversine_zero_distance():
    assert haversine_m(GG_LAT, GG_LON, GG_LAT, GG_LON) == 0


def test_haversine_known_distance():
    # 0.001 deg of latitude ≈ 111.2m
    d = haversine_m(GG_LAT, GG_LON, GG_LAT + 0.001, GG_LON)
    assert 100 < d < 120


async def test_verify_within_radius_verified():
    result = await verify_geolocation(
        {"latitude": GG_LAT + 0.0005, "longitude": GG_LON},
        {"target_latitude": GG_LAT, "target_longitude": GG_LON, "radius_m": 150},
    )
    assert result["verification_status"] == "verified"
    assert result["verification_details"]["location_passed"] is True
    assert result["verification_details"]["distance_m"] < 150


async def test_verify_outside_radius_failed():
    result = await verify_geolocation(
        {"latitude": GG_LAT + 0.01, "longitude": GG_LON},  # ~1.1km away
        {"target_latitude": GG_LAT, "target_longitude": GG_LON, "radius_m": 150},
    )
    assert result["verification_status"] == "failed"
    assert "failure_reason" in result["verification_details"]


async def test_verify_gps_accuracy_grants_bounded_allowance():
    # ~222m out with 100m reported accuracy on a 150m radius → inside
    result = await verify_geolocation(
        {"latitude": GG_LAT + 0.002, "longitude": GG_LON, "accuracy_m": 100},
        {"target_latitude": GG_LAT, "target_longitude": GG_LON, "radius_m": 150},
    )
    assert result["verification_status"] == "verified"
    # ...but a wild 10km accuracy claim is capped at radius_m and can't
    # rescue a genuinely distant reading.
    result = await verify_geolocation(
        {"latitude": GG_LAT + 0.01, "longitude": GG_LON, "accuracy_m": 10_000},
        {"target_latitude": GG_LAT, "target_longitude": GG_LON, "radius_m": 150},
    )
    assert result["verification_status"] == "failed"


async def test_verify_missing_coordinates_fails_cleanly():
    result = await verify_geolocation(
        {"longitude": GG_LON},
        {"target_latitude": GG_LAT, "target_longitude": GG_LON},
    )
    assert result["verification_status"] == "failed"
    assert "error" in result["verification_details"]


async def test_verify_default_radius_applied():
    result = await verify_geolocation(
        {"latitude": GG_LAT, "longitude": GG_LON},
        {"target_latitude": GG_LAT, "target_longitude": GG_LON},
    )
    assert result["verification_details"]["radius_m"] == 150


# ─── submit-proof route contract ───


async def test_submit_geolocation_proof_returns_202_and_dispatches():
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_active_geolocation_goal(client, token)
        with patch("app.workers.geolocation.run_geolocation_verification_task.delay") as mock_delay:
            resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={"latitude": GG_LAT, "longitude": GG_LON, "accuracy_m": 12.5},
            )
    assert resp.status_code == 202, resp.text
    assert resp.json()["verification_status"] == "pending"
    mock_delay.assert_called_once()
    kwargs = mock_delay.call_args.kwargs
    assert kwargs["proof_data"]["latitude"] == GG_LAT
    assert kwargs["criteria_data"]["target_latitude"] == GG_LAT


async def test_submit_geolocation_proof_missing_coords_422():
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_active_geolocation_goal(client, token)
        resp = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"latitude": GG_LAT},
        )
    assert resp.status_code == 422
    assert "longitude" in resp.json()["detail"]


async def test_submit_geolocation_proof_out_of_range_coords_422():
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_active_geolocation_goal(client, token)
        resp = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"latitude": 91.0, "longitude": GG_LON},
        )
    assert resp.status_code == 422


async def test_submit_youtube_proof_to_geolocation_goal_400():
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_active_geolocation_goal(client, token)
        resp = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )
    assert resp.status_code == 400


async def test_geolocation_failed_verification_dispatches_charge():
    """End-to-end within the worker: a failed location check marks the goal
    failed AND dispatches the pledge charge (via persist_verification_result)."""
    from app.config import settings
    from app.models.goal import Goal
    from app.models.proof import ProofSubmission
    from app.workers.geolocation import run_geolocation_verification
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_active_geolocation_goal(client, token)
        with patch("app.workers.geolocation.run_geolocation_verification_task.delay"):
            resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={"latitude": GG_LAT + 0.01, "longitude": GG_LON},
            )
            submission_id = resp.json()["submission_id"]

        async with session_factory() as db:
            result = await db.execute(
                select(ProofSubmission).where(ProofSubmission.id == submission_id)
            )
            submission = result.scalar_one()
            result = await db.execute(select(Goal).where(Goal.id == goal_id))
            goal = result.scalar_one()

            with patch(
                "app.workers.payments.process_charge_for_goal", new_callable=AsyncMock
            ) as mock_charge:
                await run_geolocation_verification(
                    goal_id=goal.id,
                    submission_id=submission.id,
                    proof_data=submission.proof_data,
                    criteria_data={
                        "target_latitude": GG_LAT,
                        "target_longitude": GG_LON,
                        "radius_m": 150,
                    },
                    db=db,
                )
            mock_charge.assert_awaited_once_with(str(goal.id), str(goal.user_id))

            await db.refresh(goal)
            assert goal.status == "failed"
    await engine.dispose()

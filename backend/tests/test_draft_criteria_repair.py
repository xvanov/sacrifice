"""A draft the criteria gate refuses has to be repairable.

The gate on ``PUT /api/goals/{id}`` refuses to activate a goal whose criteria
cannot be verified — right, because an unwinnable active goal is a charge at its
deadline. But it had no inverse: no endpoint accepted criteria, so the owner of a
refused draft could only delete the goal and build it again. A guard with no
remedy is a dead end, and dead ends are what make people abandon a pledge rather
than fix it.

``GoalUpdate.criteria`` is that remedy. What these tests hold in place is the two
ways it must not become something worse:

* **It must not edit a live commitment.** An active goal's criteria are what its
  pledge is measured against. An owner who could lower ``min_commits`` the night
  before the deadline has escaped the pledge without breaking a rule, which is the
  charge-evasion half of ``app/services/fault_attribution``.
* **It must not move a server-assigned value.** ``commits_since`` records when the
  goal was created. Re-stamping it discards commits the owner already made
  (a charge for work done); honouring a supplied one lets them pick a window
  covering history they already had (a free pass).
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.main import app

_FUTURE = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()


def _make_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _auth(client, email, sub):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {
            "email": email,
            "name": "Repair",
            "sub": sub,
            "picture": None,
        }
        resp = await client.post("/api/auth/google", json={"token": "valid-token"})
        assert resp.status_code in (200, 201), resp.text
        return resp.json()["access_token"]


async def _create(client, token, goal_type, criteria):
    return await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Repairable goal",
            "description": "d",
            "deadline": _FUTURE,
            "pledge_amount": 2500,
            "goal_type": goal_type,
            "criteria": criteria,
        },
    )


async def _put(client, token, goal_id, payload):
    return await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )


async def _stored(goal_id: str) -> dict:
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with engine.connect() as conn:
            row = await conn.execute(
                text("SELECT criteria_data FROM goal_criteria WHERE goal_id = :id"),
                {"id": uuid.UUID(goal_id)},
            )
            return row.scalar_one()
    finally:
        await engine.dispose()


async def _force_criteria(goal_id: str, criteria: dict) -> None:
    """Write criteria the gate would refuse — a pre-gate draft, as in production."""
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE goal_criteria SET criteria_data = CAST(:d AS JSONB) "
                    "WHERE goal_id = :id"
                ),
                {"d": json.dumps(criteria), "id": uuid.UUID(goal_id)},
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_refused_draft_can_be_repaired_and_then_activated():
    """The whole point: one request fixes the criteria and starts the goal.

    The pre-gate row here (a string ``expected_status`` the gate cannot coerce
    without guessing) is one that exists in the database today and could not be
    activated or edited by any endpoint.
    """
    async with _make_client() as client:
        token = await _auth(client, "repair-ok@example.com", "repair-ok")
        resp = await _create(
            client,
            token,
            "api_endpoint",
            {
                "url": "https://example.com/health",
                "method": "GET",
                "expected_status": 200,
            },
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]

        await _force_criteria(
            goal_id,
            {
                "url": "https://example.com/health",
                "method": "GET",
                "expected_status": "two hundred",
            },
        )

        # Refused as it stands.
        assert (
            await _put(client, token, goal_id, {"status": "active"})
        ).status_code == 422

        # Repaired and activated in one request.
        resp = await _put(
            client,
            token,
            goal_id,
            {
                "criteria": {
                    "url": "https://example.com/health",
                    "method": "GET",
                    "expected_status": 200,
                },
                "status": "active",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"
        assert (await _stored(goal_id))["expected_status"] == 200


@pytest.mark.asyncio
async def test_repairing_criteria_does_not_move_the_commit_anchor():
    """``commits_since`` survives the edit unchanged.

    Both alternatives charge somebody: re-stamping to now throws away commits the
    owner already pushed against the draft, and taking a caller-supplied value
    lets them choose a window that includes the repo's existing history.
    """
    async with _make_client() as client:
        token = await _auth(client, "repair-anchor@example.com", "repair-anchor")
        resp = await _create(
            client,
            token,
            "github_repo",
            {"repo_owner": "octocat", "repo_name": "Hello-World", "min_commits": 3},
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]
        original_anchor = (await _stored(goal_id))["commits_since"]

        resp = await _put(
            client,
            token,
            goal_id,
            {
                "criteria": {
                    "repo_owner": "octocat",
                    "repo_name": "Hello-World",
                    "min_commits": 5,
                    "commits_since": "2010-01-01T00:00:00Z",
                }
            },
        )
        assert resp.status_code == 200, resp.text

        stored = await _stored(goal_id)
        assert stored["min_commits"] == 5, "the criterion the owner edited applies"
        assert stored["commits_since"] == original_anchor, (
            "the anchor belongs to goal creation and is not the owner's to set"
        )


@pytest.mark.asyncio
async def test_criteria_cannot_be_edited_once_the_goal_is_active():
    """Moving the goalposts mid-goal is charge evasion with extra steps."""
    async with _make_client() as client:
        token = await _auth(client, "repair-active@example.com", "repair-active")
        resp = await _create(
            client,
            token,
            "github_repo",
            {"repo_owner": "octocat", "repo_name": "Hello-World", "min_commits": 5},
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]
        assert (
            await _put(client, token, goal_id, {"status": "active"})
        ).status_code == 200

        resp = await _put(
            client,
            token,
            goal_id,
            {
                "criteria": {
                    "repo_owner": "octocat",
                    "repo_name": "Hello-World",
                    "min_commits": 1,
                }
            },
        )
        assert resp.status_code == 403, resp.text
        assert "draft" in resp.json()["detail"]
        assert (await _stored(goal_id))["min_commits"] == 5, (
            "a refused edit must not partially apply"
        )


@pytest.mark.asyncio
async def test_submitted_criteria_are_gated_not_stored_blindly():
    """The repair path is a gate too, or it is a hole in the gate.

    Criteria naming nothing checkable would produce a goal its owner cannot win —
    which is what the activation gate refuses, so accepting it here would just move
    the same unwinnable goal one request later.
    """
    async with _make_client() as client:
        token = await _auth(client, "repair-gate@example.com", "repair-gate")
        resp = await _create(
            client,
            token,
            "github_repo",
            {"repo_owner": "octocat", "repo_name": "Hello-World", "min_commits": 3},
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]

        resp = await _put(
            client,
            token,
            goal_id,
            {"criteria": {"repo_owner": "octocat", "repo_name": "Hello-World"}},
        )
        assert resp.status_code == 422, resp.text
        assert "checkable" in resp.json()["detail"].lower()
        assert (await _stored(goal_id))["min_commits"] == 3, (
            "the stored criteria must be untouched when the gate refuses"
        )


@pytest.mark.asyncio
async def test_another_users_goal_is_not_reachable():
    """Ownership is checked before anything else on this route."""
    async with _make_client() as client:
        owner = await _auth(client, "repair-owner@example.com", "repair-owner")
        resp = await _create(
            client,
            owner,
            "github_repo",
            {"repo_owner": "octocat", "repo_name": "Hello-World", "min_commits": 3},
        )
        assert resp.status_code == 201, resp.text
        goal_id = resp.json()["id"]

        stranger = await _auth(client, "repair-stranger@example.com", "repair-stranger")
        resp = await _put(
            client,
            stranger,
            goal_id,
            {"criteria": {"repo_owner": "x", "repo_name": "y", "min_commits": 1}},
        )
        assert resp.status_code == 404, resp.text
        assert (await _stored(goal_id))["repo_owner"] == "octocat"

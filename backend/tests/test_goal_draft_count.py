import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.auth import _create_signed_token, ACCESS_TOKEN_PURPOSE


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ─── AC1.1: fresh user → draft count is 0 ────────────────────────────


async def test_draft_count_zero_for_newly_registered_user():
    """AC1.1: freshly registered user (zero goals) → 200 with {"count": 0}."""
    async with make_client() as client:
        register_resp = await client.post(
            "/api/auth/email/register",
            json={
                "email": f"draft-count-zero-{uuid.uuid4().hex}@test.com",
                "password": f"Ok-{uuid.uuid4().hex}",
                "display_name": "DraftCounter",
            },
        )
        assert register_resp.status_code == 200
        token = register_resp.json()["access_token"]

        count_resp = await client.get(
            "/api/goals/draft-count",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert count_resp.status_code == 200
    body = count_resp.json()
    assert body == {"count": 0}


# ─── AC2.1: draft count reflects a newly created goal ────────────────


async def test_draft_count_increases_after_goal_creation():
    """AC2.1: creating one goal (defaults to draft) → draft count is 1."""
    async with make_client() as client:
        register_resp = await client.post(
            "/api/auth/email/register",
            json={
                "email": f"draft-count-one-{uuid.uuid4().hex}@test.com",
                "password": f"Ok-{uuid.uuid4().hex}",
                "display_name": "DraftCounterOne",
            },
        )
        assert register_resp.status_code == 200
        token = register_resp.json()["access_token"]

        # Baseline: zero drafts
        baseline_resp = await client.get(
            "/api/goals/draft-count",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert baseline_resp.status_code == 200
        assert baseline_resp.json() == {"count": 0}

        # Create a goal — status defaults to "draft" (GoalCreate has no status field)
        deadline = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": f"{uuid.uuid4().hex}-draft-count-check",
                "deadline": deadline,
                "pledge_amount": 500,
                "goal_type": "api_endpoint",
                "criteria": {
                    "url": "https://example.com/health",
                    "method": "GET",
                    "expected_status": 200,
                },
            },
        )
        assert create_resp.status_code == 201

        # Now draft count should be 1
        count_resp = await client.get(
            "/api/goals/draft-count",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert count_resp.status_code == 200
    assert count_resp.json() == {"count": 1}


# ─── AC3.1: unauthenticated → 401 (no Authorization header) ──────────


async def test_draft_count_rejected_without_auth_header():
    """AC3.1: no Authorization header → 401."""
    async with make_client() as client:
        resp = await client.get("/api/goals/draft-count")
    assert resp.status_code == 401


# ─── AC3.2: expired token → 401 ──────────────────────────────────────


async def test_draft_count_rejected_with_expired_token():
    """AC3.2: expired token → 401."""
    async with make_client() as client:
        # Register a user first so the token references a real user
        reg_resp = await client.post(
            "/api/auth/email/register",
            json={
                "email": f"expired-token-{uuid.uuid4().hex}@test.com",
                "password": f"Ok-{uuid.uuid4().hex}",
                "display_name": "ExpiredTokenUser",
            },
        )
        assert reg_resp.status_code == 200
        real_user_id = reg_resp.json()["user"]["id"]
        expired_token = _create_signed_token(
            real_user_id,
            purpose=ACCESS_TOKEN_PURPOSE,
            expires_in=timedelta(minutes=-60),
            extra_claims={"sid": "dead-session-id"},
        )
        resp = await client.get(
            "/api/goals/draft-count",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
    assert resp.status_code == 401


# ─── AC3.3: malformed token → 401 ────────────────────────────────────


async def test_draft_count_rejected_with_malformed_token():
    """AC3.3: malformed token → 401."""
    async with make_client() as client:
        resp = await client.get(
            "/api/goals/draft-count",
            headers={"Authorization": "Bearer not-a-valid-jwt"},
        )
    assert resp.status_code == 401
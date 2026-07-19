"""Tests for JSON payload guard — size and depth limits."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(
    client, email="test@example.com", name="Test User", sub="test-sub-123", token="valid-token"
):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


def _create_youtube_goal():
    return {
        "title": "My YouTube Goal",
        "description": "Record a walkthrough",
        "deadline": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        "pledge_amount": 5000,
        "goal_type": "youtube_video",
        "criteria": {
            "min_duration_seconds": 120,
            "video_description": "A walkthrough demo",
        },
        "charity_id": "acct_charity123",
    }


async def _create_goal_and_activate(client, token):
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json=_create_youtube_goal(),
    )
    goal_id = resp.json()["id"]
    await client.put(
        f"/api/goals/{goal_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )
    return goal_id


class TestPayloadGuardDirect:
    """Unit tests for the payload guard utility directly."""

    def test_accepts_normal_payload(self):
        from app.core.payload_guard import validate_json_payload

        body = {"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
        validate_json_payload(body, max_size_bytes=1_048_576, max_depth=10)

    def test_rejects_oversized_payload(self):
        from app.core.payload_guard import PayloadTooLargeError, validate_json_payload

        # Create a payload that's definitely over 100 bytes when serialized
        body = {"data": "x" * 200}
        with pytest.raises(PayloadTooLargeError) as exc:
            validate_json_payload(body, max_size_bytes=100, max_depth=10)
        assert "exceeds maximum size" in str(exc.value)

    def test_rejects_deeply_nested_payload(self):
        from app.core.payload_guard import PayloadTooDeepError, validate_json_payload

        # Create deeply nested dict
        body = {}
        current = body
        for _ in range(20):
            current["nested"] = {}
            current = current["nested"]
        current["value"] = 1

        with pytest.raises(PayloadTooDeepError) as exc:
            validate_json_payload(body, max_size_bytes=1_048_576, max_depth=10)
        assert "exceeds maximum nesting depth" in str(exc.value)

    def test_accepts_payload_at_depth_limit(self):
        from app.core.payload_guard import validate_json_payload

        body = {}
        current = body
        for _ in range(5):
            current["nested"] = {}
            current = current["nested"]
        current["value"] = 1

        # Should not raise — depth is 6 (root + 5 nested)
        validate_json_payload(body, max_size_bytes=1_048_576, max_depth=10)

    def test_accepts_payload_at_size_limit(self):
        from app.core.payload_guard import validate_json_payload

        body = {"key": "value"}
        serialized = json.dumps(body).encode("utf-8")
        size = len(serialized)

        # Should not raise when exactly at limit
        validate_json_payload(body, max_size_bytes=size, max_depth=10)

    def test_rejects_payload_one_byte_over_size_limit(self):
        from app.core.payload_guard import PayloadTooLargeError, validate_json_payload

        body = {"key": "value"}
        serialized = json.dumps(body).encode("utf-8")
        size = len(serialized)

        with pytest.raises(PayloadTooLargeError):
            validate_json_payload(body, max_size_bytes=size - 1, max_depth=10)

    def test_shallow_list_nesting_accepted(self):
        from app.core.payload_guard import validate_json_payload

        body = {"items": [1, 2, 3], "meta": {"nested": {"deep": [{"a": 1}]}}}
        # depth = 5 (root -> meta -> nested -> deep -> list[0] -> a)
        validate_json_payload(body, max_size_bytes=1_048_576, max_depth=10)

    def test_deep_list_nesting_rejected(self):
        from app.core.payload_guard import PayloadTooDeepError, validate_json_payload

        # Build a payload that alternates dict/list to hit depth 22
        body: dict = {"root": []}
        current: list = body["root"]
        for _ in range(21):
            current.append({})
            current = current[0]
            if isinstance(current, dict):
                current["next"] = []
                current = current["next"]
        # One more level to push past depth 10
        current.append({"deep": "value"})

        with pytest.raises(PayloadTooDeepError):
            validate_json_payload(body, max_size_bytes=1_048_576, max_depth=10)


# ── Route-level tests: proof submission payload guard ─────────────────────


@pytest.mark.asyncio
async def test_proof_submission_rejects_oversized_json_payload():
    """AC1.1: Proof endpoint rejects oversized JSON payload with 413."""
    from functools import partial
    from unittest.mock import patch

    from app.core.payload_guard import validate_json_payload as real_validate

    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        # Build a payload that is >100 bytes when serialized
        large_payload = {
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "extra": "x" * 200,
        }

        # Patch validate_json_payload in the goals module so the route calls
        # the real guard with a small max_size_bytes, exercising the full
        # route → guard → HTTPException(413) path.
        patched_validate = partial(real_validate, max_size_bytes=100, max_depth=10)
        with patch("app.routes.goals.validate_json_payload", patched_validate):
            response = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json=large_payload,
            )

        assert response.status_code == 413
        assert "exceeds maximum size" in response.json()["detail"]


@pytest.mark.asyncio
async def test_proof_submission_rejects_deeply_nested_json_payload():
    """AC1.2: Proof endpoint rejects deeply nested JSON payload with 422."""
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        # Build a deeply nested payload (depth > 10)
        nested: dict = {}
        current = nested
        for _ in range(15):
            current["nested"] = {}
            current = current["nested"]
        current["youtube_url"] = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        response = await client.post(
            f"/api/goals/{goal_id}/submit-proof",
            headers={"Authorization": f"Bearer {token}"},
            json=nested,
        )

        assert response.status_code == 422
        assert "nesting depth" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_proof_submission_accepts_normal_json_payload():
    """Proof endpoint accepts normal-sized, normal-depth JSON payload."""
    async with make_client() as client:
        token, _ = await _auth(client)
        goal_id = await _create_goal_and_activate(client, token)

        with patch("app.workers.youtube.run_youtube_verification_task.delay"):
            response = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            )

        assert response.status_code == 202
        assert "submission_id" in response.json()

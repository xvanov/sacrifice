"""
Tests that the deadline worker skips goals in ``awaiting_goal_type`` status.

All tests MUST fail on first run because:
- ``awaiting_goal_type`` is not in the Goal.status enum
- The deadline worker's query filters for status='active' and
  status='pending_review' but has no explicit skip for awaiting_goal_type
"""

from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


VALID_GOAL = {
    "title": "20 morning pushups",
    "description": "Do 20 pushups every morning at 7am.",
    "deadline": "2020-01-01T00:00:00Z",  # way in the past
    "pledge_amount": 1000,
    "goal_type": "youtube_video",
    "criteria": {"min_duration_seconds": 300, "video_description": "A walkthrough demo"},
    "charity_id": "acct_charity123",
}


async def test_deadline_worker_skips_awaiting_goal_type():
    """
    The deadline worker does NOT charge or fail goals in awaiting_goal_type status,
    even when their deadline has passed. The worker's query must explicitly
    exclude awaiting_goal_type goals.
    MUST fail: awaiting_goal_type doesn't exist yet, so we cannot even create
    a goal in that status.
    """
    async with make_client() as client:
        token, _ = await _auth(client)

        # Create a goal that's already past its deadline
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        assert create_resp.status_code == 201
        goal_id = create_resp.json()["id"]

        # Try to set it to awaiting_goal_type — must fail pre-impl
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "awaiting_goal_type"},
        )
        assert resp.status_code == 200

        # Run the deadline check
        from app.workers.deadline import check_deadlines

        result = await check_deadlines()

        # The awaiting_goal_type goal must NOT be in processed counts
        detail = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert detail.status_code == 200
        # Status must still be awaiting_goal_type — not failed
        assert detail.json()["status"] == "awaiting_goal_type"


async def test_deadline_worker_has_status_specific_queries():
    """
    The deadline worker's check_deadlines() should NOT use a broad
    ``WHERE status != 'awaiting_goal_type'`` or ``WHERE status IN (...)``
    pattern that could accidentally sweep in new statuses. It must use
    explicit, positive status equality checks.

    This test inspects the worker's source for 'awaiting_goal_type'
    — absence means the worker's existing query pattern (status =
    specific_values) already excludes it by default.
    """
    from app.workers.deadline import check_deadlines
    import inspect

    source = inspect.getsource(check_deadlines)

    # The current worker uses status = 'active' and status = 'pending_review'
    # These explicit equality checks mean awaiting_goal_type is skipped by
    # construction — no change needed. This test documents that.
    assert "status = 'active'" in source
    assert "status = 'pending_review'" in source

    # awaiting_goal_type is NOT mentioned — it's excluded by default.
    # If someone adds it, this test forces them to think about whether
    # it belongs in the deadline path.
    assert "awaiting_goal_type" not in source, (
        "Deadline worker mentions awaiting_goal_type — ensure it's excluded, not included"
    )
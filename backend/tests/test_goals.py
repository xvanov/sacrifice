from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None, "email_verified": True}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


VALID_GOAL = {
    "title": "Ship the MVP",
    "description": "Launch the sacrifice app",
    "deadline": "2026-06-01T00:00:00Z",
    "pledge_amount": 5000,
    "goal_type": "youtube_video",
    "criteria": {"min_duration_seconds": 300, "video_description": "A walkthrough demo"},
    "charity_id": "acct_charity123",
}


async def test_create_goal_returns_201_with_id():
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["title"] == "Ship the MVP"
    assert body["pledge_amount"] == 5000
    assert body["goal_type"] == "youtube_video"
    assert body["status"] == "draft"
    assert "criteria" in body
    assert body["criteria"]["criteria_data"]["min_duration_seconds"] == 300
    assert body["criteria"]["criteria_type"] == "youtube"


async def test_create_goal_without_required_fields_returns_422():
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
    assert response.status_code == 422


async def test_get_goals_returns_only_authenticated_user_goals():
    async with make_client() as client:
        token1, _ = await _auth(client)
        await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token1}"},
            json=VALID_GOAL,
        )

        token2, _ = await _auth(client, email="other@test.com", name="Other",
                                sub="other-sub", token="other-token")
        await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token2}"},
            json={**VALID_GOAL, "title": "Other Goal"},
        )

        response = await client.get(
            "/api/goals",
            headers={"Authorization": f"Bearer {token1}"},
        )
    goals = response.json()
    assert len(goals) == 1
    assert goals[0]["title"] == "Ship the MVP"


async def test_get_goals_filter_by_status():
    async with make_client() as client:
        token, _ = await _auth(client)
        await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )

        response = await client.get(
            "/api/goals?status=active",
            headers={"Authorization": f"Bearer {token}"},
        )
    goals = response.json()
    for g in goals:
        assert g["status"] == "active"


async def test_get_goal_by_id_returns_goal_for_owner():
    async with make_client() as client:
        token, _ = await _auth(client)
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        goal_id = create_resp.json()["id"]

        response = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["id"] == goal_id


async def test_get_goal_by_id_returns_404_for_other_user():
    async with make_client() as client:
        token1, _ = await _auth(client)
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token1}"},
            json=VALID_GOAL,
        )
        goal_id = create_resp.json()["id"]

        token2, _ = await _auth(client, email="other@test.com", name="Other",
                                sub="other-sub", token="other-token")
        response = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token2}"},
        )
    assert response.status_code == 404


async def test_update_goal_updates_fields():
    async with make_client() as client:
        token, _ = await _auth(client)
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        goal_id = create_resp.json()["id"]

        response = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Updated Title", "pledge_amount": 10000},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Updated Title"
    assert body["pledge_amount"] == 10000


async def test_update_goal_rejects_edit_after_non_editable_status():
    async with make_client() as client:
        token, _ = await _auth(client)
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        goal_id = create_resp.json()["id"]

        # Draft is editable — update title
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Draft Editable"},
        )
        assert resp.status_code == 200

        # Transition to active — still editable
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )
        assert resp.status_code == 200
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Active Editable"},
        )
        assert resp.status_code == 200

        # An active goal is BINDING: the user cannot cancel it (that would
        # escape the pledge). Only drafts can be cancelled.
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "cancelled"},
        )
        assert resp.status_code == 403

        # Reach a non-editable terminal state the legitimate way: cancel a
        # fresh draft, then confirm it can no longer be edited.
        draft_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        draft_id = draft_resp.json()["id"]
        resp = await client.put(
            f"/api/goals/{draft_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "cancelled"},
        )
        assert resp.status_code == 200

        response = await client.put(
            f"/api/goals/{draft_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Should not update"},
        )
    assert response.status_code == 400


async def test_delete_goal_removes_draft_goal():
    async with make_client() as client:
        token, _ = await _auth(client)
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        goal_id = create_resp.json()["id"]

        delete_resp = await client.delete(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert delete_resp.status_code == 204

        get_resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_resp.status_code == 404


async def test_delete_goal_non_draft_returns_404():
    async with make_client() as client:
        token, _ = await _auth(client)
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        goal_id = create_resp.json()["id"]

        await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )

        response = await client.delete(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 400


async def test_cannot_transition_active_to_verified_without_pending_review():
    async with make_client() as client:
        token, _ = await _auth(client)
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        goal_id = create_resp.json()["id"]

        await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )

        response = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "verified"},
        )
    # 403: self-verifying is forbidden, not merely an invalid transition.
    assert response.status_code == 403


async def test_user_cannot_escape_pledge_via_status_puts():
    """Regression: the owner must not be able to resolve their own active goal.

    Previously ALLOWED_TRANSITIONS permitted active→pending_review→verified
    and active→cancelled, and the PUT endpoint enforced only ownership — so an
    owner about to miss a deadline could mark the goal verified (claiming
    success with no proof) or cancel it, escaping the pledge entirely.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        create_resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        goal_id = create_resp.json()["id"]
        await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )

        for forbidden in ("pending_review", "verified", "failed", "cancelled"):
            resp = await client.put(
                f"/api/goals/{goal_id}",
                headers={"Authorization": f"Bearer {token}"},
                json={"status": forbidden},
            )
            assert resp.status_code == 403, (
                f"user was allowed to move active goal to {forbidden!r}"
            )

        # The goal is still active — no self-service resolution happened.
        got = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert got.json()["status"] == "active"


async def test_create_goal_rejects_unregistered_goal_type():
    """Regression: an unknown goal_type (e.g. 'api' vs 'api_endpoint') must be
    rejected at creation, not create a goal that can never be fulfilled."""
    async with make_client() as client:
        token, _ = await _auth(client)
        bad = dict(VALID_GOAL)
        bad["goal_type"] = "api"  # not registered; correct name is api_endpoint
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=bad,
        )
        assert resp.status_code == 422

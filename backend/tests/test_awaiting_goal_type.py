"""Tests for awaiting_goal_type status, direction linkage, and related behaviors.

These tests assert on production code that does NOT exist yet. Every test
in this file MUST fail (RED) on first run against the current codebase.

Covers:
- Model: awaiting_goal_type in Goal.status enum (tested via app-layer persistence)
- Model: nullable awaiting_direction_id column on goals
- Schema: GoalResponse exposes awaiting_direction_id
- Service: ALLOWED_TRANSITIONS for awaiting_goal_type
- Worker: deadline worker skips awaiting_goal_type goals
- Notification: goal_type_ready notification type
"""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.services import direction_synth as _direction_synth


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


async def _seed_session(sf, user_id, session_id):
    """Create a minimal goal to establish a chat session for the user."""
    async with sf() as db:
        await db.execute(
            text("""
                INSERT INTO goals
                    (id, user_id, title, description, goal_type, pledge_amount,
                     currency, deadline, timezone, recurrence, status,
                     session_id, charity_id, created_at, updated_at)
                VALUES
                    (:id, :user_id, :title, :desc, :gtype, :amt,
                     :cur, :dl, :tz, :rec, :status,
                     :sid, :cid, now(), now())
            """),
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "title": "Session seed",
                "desc": "",
                "gtype": "youtube_video",
                "amt": 0,
                "cur": "usd",
                "dl": datetime(2026, 6, 1, tzinfo=timezone.utc),
                "tz": "UTC",
                "rec": "none",
                "status": "draft",
                "sid": session_id,
                "cid": None,
            },
        )
        await db.commit()


VALID_GOAL = {
    "title": "Ship the MVP",
    "description": "Launch the sacrifice app",
    "deadline": "2026-06-01T00:00:00Z",
    "pledge_amount": 5000,
    "goal_type": "youtube_video",
    "criteria": {"min_duration_seconds": 300, "video_description": "A walkthrough demo"},
    "charity_id": "acct_charity123",
}


# ─── Goal creation via API with awaiting_goal_type ──────────────────


async def test_create_goal_via_chat_endpoint_produces_awaiting_goal_type(tmp_path):
    """POST request-new-goal-type must create goal with awaiting_goal_type status."""
    session_id = str(uuid.uuid4())

    mock_llm_response = """---
title: Pushup Counter
type: feature
why: Users need pushup verification via phone camera
acceptance: |
  - verify(criteria={"count":20}, upload=pushups_20.mp4) → verified
---

# Pushup Counter
"""

    async with make_client() as client:
        token, user = await _auth(client)

        sf = async_sessionmaker(
            create_async_engine(settings.database_url, echo=False),
            class_=AsyncSession, expire_on_commit=False,
        )
        await _seed_session(sf, user["id"], session_id)

        with patch(
            "app.routes.chat.settings.directions_output_path", str(tmp_path)
        ):
            mock_llm = AsyncMock(return_value=mock_llm_response)

            with patch.object(
                _direction_synth, "_call_llm", mock_llm
            ):
                resp = await client.post(
                    f"/api/chat/sessions/{session_id}/request-new-goal-type",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "prompt_summary": "Do 20 pushups every morning at 7am verified with my phone camera",
                        "goal_payload_draft": {
                            "title": "20 morning pushups",
                            "description": "Do 20 pushups every morning at 7am",
                            "pledge_amount": 1000,
                            "currency": "usd",
                            "deadline": "2026-05-26T11:00:00Z",
                            "timezone": "America/New_York",
                            "charity_id": "acct_charity123",
                            "recurrence": "daily",
                        },
                    },
                )

        assert resp.status_code == 202
        body = resp.json()
        goal_id = body["goal_id"]
        direction_id = body["direction_id"]

    # Verify filesystem side effects — direction directory and files exist
    direction_dir = tmp_path / direction_id
    assert direction_dir.is_dir(), f"Expected direction dir at {direction_dir}"

    # Assert the written directory is rooted under the configured output path
    assert str(direction_dir).startswith(str(tmp_path)), (
        f"Direction dir '{direction_dir}' must be rooted under "
        f"configured directions output path '{tmp_path}'"
    )

    # Assert the returned direction_id matches the directory name exactly
    assert direction_dir.name == direction_id, (
        f"Directory name '{direction_dir.name}' must match "
        f"returned direction_id '{direction_id}'"
    )

    direction_md = direction_dir / "direction.md"
    assert direction_md.is_file(), f"Expected direction.md at {direction_md}"
    md_content = direction_md.read_text()
    assert "title:" in md_content
    assert "type:" in md_content
    assert "why:" in md_content
    assert "acceptance:" in md_content

    state_yaml = direction_dir / "state.yaml"
    assert state_yaml.is_file(), f"Expected state.yaml at {state_yaml}"
    state_content = state_yaml.read_text()
    assert "status: queued" in state_content
    assert f"direction_id: {direction_id}" in state_content
    assert "created_at:" in state_content

    # Verify via DB
    engine = create_async_engine(settings.database_url, echo=False)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        result = await db.execute(
            text("SELECT status, awaiting_direction_id FROM goals WHERE id = :id"),
            {"id": goal_id},
        )
        row = result.one_or_none()
        assert row is not None
        assert row[0] == "awaiting_goal_type"
        assert row[1] == direction_id
    await engine.dispose()


async def test_request_new_goal_type_uses_global_counter_not_local_sequence(tmp_path):
    """When unrelated direction directories already exist with higher ids,
    the returned direction_id must use the next global allocated id (not
    assume local sequentiality)."""
    session_id = str(uuid.uuid4())

    # Pre-populate unrelated direction directories with higher ids
    for did in ("005-existing-feature", "017-another-one", "042-something-else"):
        (tmp_path / did).mkdir(parents=True)
        (tmp_path / did / "state.yaml").write_text(
            f"status: pr_merged\ndirection_id: {did}\n"
        )
    # Also pre-populate a counter file with a low value
    (tmp_path / ".direction_counter").write_text("3")

    mock_llm_response = """---
title: Pushup Counter
type: feature
why: Users need pushup verification via phone camera
acceptance: |
  - verify(criteria={"count":20}, upload=pushups_20.mp4) → verified
---

# Pushup Counter
"""

    async with make_client() as client:
        token, user = await _auth(client)

        sf = async_sessionmaker(
            create_async_engine(settings.database_url, echo=False),
            class_=AsyncSession, expire_on_commit=False,
        )
        await _seed_session(sf, user["id"], session_id)

        with patch(
            "app.routes.chat.settings.directions_output_path", str(tmp_path)
        ):
            mock_llm = AsyncMock(return_value=mock_llm_response)

            with patch.object(
                _direction_synth, "_call_llm", mock_llm
            ):
                resp = await client.post(
                    f"/api/chat/sessions/{session_id}/request-new-goal-type",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "prompt_summary": "Do 20 pushups every morning at 7am verified with my phone camera",
                        "goal_payload_draft": VALID_GOAL,
                    },
                )

    assert resp.status_code == 202
    body = resp.json()
    direction_id = body["direction_id"]

    # The next id should be 043 (max(42, 3) + 1), NOT 004 or 001
    expected_prefix = "043-"
    assert direction_id.startswith(expected_prefix), (
        f"Expected direction_id to start with '{expected_prefix}' "
        f"(next global id after 042), got '{direction_id}'"
    )

    # The id must NOT be sequential from the counter (004) or from scratch (001)
    assert not direction_id.startswith("004-"), (
        f"direction_id must not be locally sequential from counter; got '{direction_id}'"
    )
    assert not direction_id.startswith("001-"), (
        f"direction_id must not start from 001 when higher dirs exist; got '{direction_id}'"
    )


# ─── GoalResponse exposes awaiting_direction_id ────────────────────


async def test_goal_response_includes_awaiting_direction_id(tmp_path):
    """GoalResponse must expose awaiting_direction_id when present."""
    session_id = str(uuid.uuid4())

    mock_llm_response = """---
title: Test Type
type: feature
why: testing
acceptance: |
  - test criterion
---
"""

    async with make_client() as client:
        token, user = await _auth(client)

        sf = async_sessionmaker(
            create_async_engine(settings.database_url, echo=False),
            class_=AsyncSession, expire_on_commit=False,
        )
        await _seed_session(sf, user["id"], session_id)

        with patch(
            "app.routes.chat.settings.directions_output_path", str(tmp_path)
        ):
            mock_llm = AsyncMock(return_value=mock_llm_response)

            with patch.object(
                _direction_synth, "_call_llm", mock_llm
            ):
                resp = await client.post(
                    f"/api/chat/sessions/{session_id}/request-new-goal-type",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "prompt_summary": "Do 20 pushups every morning verified with camera",
                        "goal_payload_draft": {
                            "title": "Test goal",
                            "description": "test",
                            "pledge_amount": 1000,
                            "currency": "usd",
                            "deadline": "2026-05-26T11:00:00Z",
                            "timezone": "UTC",
                            "charity_id": None,
                            "recurrence": "none",
                        },
                    },
                )

        assert resp.status_code == 202
        goal_id = resp.json()["goal_id"]
        direction_id = resp.json()["direction_id"]

        # Read via GET
        resp2 = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp2.status_code == 200
        body = resp2.json()
        assert "awaiting_direction_id" in body
        assert body["awaiting_direction_id"] == direction_id


async def test_goal_response_awaiting_direction_id_null_when_unset():
    """GoalResponse awaiting_direction_id must be null for goals without one."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        goal_id = resp.json()["id"]

        resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "awaiting_direction_id" in body
        assert body["awaiting_direction_id"] is None


# ─── Service-layer: ALLOWED_TRANSITIONS ───────────────────────────────

# Tested via the API rather than in-process to exercise real application paths.


async def test_awaiting_goal_type_transitions_to_active_via_api():
    """Goal in awaiting_goal_type must be able to transition to active via API."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        goal_id = resp.json()["id"]

        # Transition to awaiting_goal_type via API update
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "awaiting_goal_type"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "awaiting_goal_type"

        # Transition to active
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"


async def test_awaiting_goal_type_cannot_transition_to_verified_via_api():
    """Goal in awaiting_goal_type must NOT transition directly to verified."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        goal_id = resp.json()["id"]

        # Transition to awaiting_goal_type
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "awaiting_goal_type"},
        )
        assert resp.status_code == 200

        # Attempt transition to verified — should fail
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "verified"},
        )
        assert resp.status_code == 400


# ─── Worker: deadline worker skips awaiting_goal_type goals ──────────


async def test_deadline_worker_skips_awaiting_goal_type_goals():
    """check_deadlines must not charge or fail goals in awaiting_goal_type."""
    from app.workers.deadline import check_deadlines

    async with make_client() as client:
        token, user = await _auth(client)

        past_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                **VALID_GOAL,
                "deadline": past_deadline,
            },
        )
        goal_id = resp.json()["id"]

        # Set to awaiting_goal_type via API
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "awaiting_goal_type"},
        )
        assert resp.status_code == 200

        # Set awaiting_direction_id via DB
        engine = create_async_engine(settings.database_url, echo=False)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sf() as db:
            await db.execute(
                text("UPDATE goals SET awaiting_direction_id = :did WHERE id = :id"),
                {"did": "011-pushup-counter", "id": goal_id},
            )
            await db.commit()
        await engine.dispose()

        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            await check_deadlines()
            mock_charge.assert_not_called()

        # Verify goal NOT changed to failed
        engine2 = create_async_engine(settings.database_url, echo=False)
        sf2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
        async with sf2() as db:
            result = await db.execute(
                text("SELECT status FROM goals WHERE id = :id"),
                {"id": goal_id},
            )
            assert result.scalar() == "awaiting_goal_type"
        await engine2.dispose()


# ─── Notification: goal_type_ready notification type ──────────────────


async def test_notification_enum_includes_goal_type_ready():
    """Notification.type enum must include 'goal_type_ready'."""
    from app.services.notification import create_notification

    user_id = uuid.uuid4()

    engine = create_async_engine(settings.database_url, echo=False)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        await db.execute(
            text("""
                INSERT INTO users (id, email, display_name, auth_provider, auth_provider_id, created_at)
                VALUES (:id, :email, :name, :provider, :pid, now())
            """),
            {
                "id": user_id,
                "email": "notiftype@example.com",
                "name": "Notif Type",
                "provider": "google",
                "pid": "google-notiftype-1",
            },
        )
        await db.commit()

        notif = await create_notification(
            db=db,
            user_id=user_id,
            notification_type="goal_type_ready",
            title="Goal Type Ready",
            body="Your pushup-counter goal type is ready.",
            goal_id=None,
        )

    assert notif is not None
    assert notif.id is not None
    assert notif.type == "goal_type_ready"
    assert notif.title == "Goal Type Ready"

    await engine.dispose()


# ─── Existing goal statuses remain unchanged ──────────────────────────

# Tested via the API to exercise real schema and service validation.


async def test_existing_goal_statuses_still_accepted():
    """All existing goal statuses must be persistable via API."""
    async with make_client() as client:
        token, _ = await _auth(client)

        # Create a goal with default draft status
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        assert resp.status_code == 201
        goal_id = resp.json()["id"]

        # Verify draft is read back
        resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json()["status"] == "draft"

        # The new awaiting_goal_type should be accepted as a valid status
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "awaiting_goal_type"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "awaiting_goal_type"
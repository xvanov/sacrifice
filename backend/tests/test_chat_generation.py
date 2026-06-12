"""Tests for chat generation endpoints.

These tests assert on endpoints that do NOT exist yet. Every test in this
file MUST fail (RED) on first run against the current codebase.

Covers:
- POST /api/chat/sessions/{session_id}/request-new-goal-type
- GET /api/chat/sessions/{session_id}/generation-status
- POST /api/chat/sessions/{session_id}/accept-generated-type
- POST /api/chat/sessions/{session_id}/iterate-generated-type

All tests drive the real endpoint and assert on observable side-effects
(persisted goal, direction directory, ledger rows, state.yaml parsing),
NOT on mocked return values from route helpers.
"""

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.services import direction_synth as _direction_synth


# ── helpers ──────────────────────────────────────────────────────────


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
    "description": "Do 20 pushups every morning at 7am, verified with my phone camera.",
    "pledge_amount": 1000,
    "currency": "usd",
    "deadline": "2026-05-26T11:00:00Z",
    "timezone": "America/New_York",
    "charity_id": "acct_charity123",
    "recurrence": "daily",
}


def _make_session_id():
    return str(uuid.uuid4())


# ─── POST /api/chat/sessions/{session_id}/request-new-goal-type ──────


async def test_request_new_goal_type_returns_202_on_success(tmp_path):
    """request-new-goal-type must persist goal, write direction dir, record spend."""
    session_id = _make_session_id()

    mock_llm_response = """---
title: Pushup Counter
type: feature
why: Users need pushup verification via phone camera
acceptance: |
  - verify(criteria={"count":20}, upload=pushups_20.mp4) → verified
  - verify(criteria={"count":25}, upload=pushups_20.mp4) → failed
---

# Pushup Counter

## What
A goal type that counts pushups from a phone camera video.
"""

    async with make_client() as client:
        token, user = await _auth(client)

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
        assert "direction_id" in body
        assert "goal_id" in body
        assert body["status"] == "queued"
        assert body["direction_id"].endswith("-pushup-counter")

        # Assert goal persisted in DB with awaiting_goal_type status
        engine = create_async_engine(settings.database_url, echo=False)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as db:
            result = await db.execute(
                text("SELECT status, awaiting_direction_id FROM goals WHERE id = :id"),
                {"id": body["goal_id"]},
            )
            row = result.one_or_none()
            assert row is not None
            assert row[0] == "awaiting_goal_type"
            assert row[1] == body["direction_id"]

            # Assert ledger recorded (direction_id embedded in call_description)
            result2 = await db.execute(
                text("SELECT COUNT(*) FROM chat_spend_ledger WHERE call_description LIKE :pat"),
                {"pat": f"%:{body['direction_id']}"},
            )
            assert result2.scalar() == 1
        await engine.dispose()

        # Assert direction directory created
        direction_dir = tmp_path / body["direction_id"]
        assert direction_dir.is_dir()
        assert (direction_dir / "direction.md").is_file()
        assert (direction_dir / "state.yaml").is_file()


async def test_request_new_goal_type_returns_401_without_auth():
    """request-new-goal-type must reject unauthenticated requests."""
    session_id = _make_session_id()

    async with make_client() as client:
        resp = await client.post(
            f"/api/chat/sessions/{session_id}/request-new-goal-type",
            json={
                "prompt_summary": "Do 20 pushups",
                "goal_payload_draft": VALID_GOAL,
            },
        )

    assert resp.status_code == 401


async def test_request_new_goal_type_returns_409_when_generation_in_flight(tmp_path):
    """request-new-goal-type must return 409 when user already has an awaiting_goal_type goal."""
    session_id = _make_session_id()

    mock_llm_response = """---
title: First Goal Type
type: feature
why: Testing in-flight conflict
acceptance: |
  - test criterion
---
"""
    async with make_client() as client:
        token, user = await _auth(client)

        with patch(
            "app.routes.chat.settings.directions_output_path", str(tmp_path)
        ):
            mock_llm = AsyncMock(return_value=mock_llm_response)

            with patch.object(
                _direction_synth, "_call_llm", mock_llm
            ):
                # First request — should succeed
                resp1 = await client.post(
                    f"/api/chat/sessions/{session_id}/request-new-goal-type",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "prompt_summary": "Do 20 pushups every morning at 7am verified with my phone camera",
                        "goal_payload_draft": VALID_GOAL,
                    },
                )
                assert resp1.status_code == 202
                direction_id = resp1.json()["direction_id"]

                # Second request — should return 409 with existing direction_id
                resp2 = await client.post(
                    f"/api/chat/sessions/{session_id}/request-new-goal-type",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "prompt_summary": "Do 30 sit-ups every evening",
                        "goal_payload_draft": VALID_GOAL,
                    },
                )

        assert resp2.status_code == 409
        body = resp2.json()
        assert "direction_id" in body
        assert body["direction_id"] == direction_id


async def test_request_new_goal_type_returns_422_for_vague_prompt(tmp_path):
    """request-new-goal-type must return 422 when prompt is too vague for synthesis."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, _ = await _auth(client)

        with patch(
            "app.routes.chat.settings.directions_output_path", str(tmp_path)
        ):
            # Don't mock the LLM — let _is_vague() reject it at the service layer
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": "do something",
                    "goal_payload_draft": VALID_GOAL,
                },
            )

        assert resp.status_code == 422

        # Assert no direction directory was created
        entries = list(tmp_path.iterdir())
        assert len(entries) == 0, f"Expected no direction dirs, found {entries}"

        # Assert no goal was created
        engine = create_async_engine(settings.database_url, echo=False)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as db:
            result = await db.execute(
                text("SELECT COUNT(*) FROM goals WHERE status = 'awaiting_goal_type'")
            )
            assert result.scalar() == 0
        await engine.dispose()


async def test_request_new_goal_type_returns_429_when_spend_cap_hit(tmp_path):
    """request-new-goal-type must return 429 when daily AI budget is exhausted."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, user = await _auth(client)

        # Seed spend ledger to the cap
        engine = create_async_engine(settings.database_url, echo=False)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as db:
            await db.execute(
                text("""
                    INSERT INTO chat_spend_ledger
                        (id, user_id, model, cost_millicents, call_description, call_timestamp)
                    VALUES
                        (:id, :user_id, :model, :cost_millicents, :call_description, now())
                """),
                {
                    "id": uuid.uuid4(),
                    "user_id": user["id"],
                    "model": settings.direction_synth_model,
                    "cost_millicents": settings.chat_daily_spend_cap_millicents,
                    "call_description": "direction_synthesis:old-direction",
                },
            )
            await db.commit()
        await engine.dispose()

        with patch(
            "app.routes.chat.settings.directions_output_path", str(tmp_path)
        ):
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": "Do 20 pushups every morning at 7am verified with my phone camera",
                    "goal_payload_draft": VALID_GOAL,
                },
            )

        assert resp.status_code == 429
        body = resp.json()
        assert "budget" in body.get("detail", "").lower()

        # Assert no goal was created
        engine2 = create_async_engine(settings.database_url, echo=False)
        sf2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
        async with sf2() as db:
            result = await db.execute(
                text("SELECT COUNT(*) FROM goals WHERE user_id = :uid AND status = 'awaiting_goal_type'"),
                {"uid": user["id"]},
            )
            assert result.scalar() == 0
        await engine2.dispose()

        # Assert no direction dirs were written
        entries = list(tmp_path.iterdir())
        assert len(entries) == 0


# ─── GET /api/chat/sessions/{session_id}/generation-status ────────────


async def test_generation_status_returns_200_with_status_fields(tmp_path):
    """generation-status must read state.yaml and return coarse status."""
    session_id = _make_session_id()

    # Create a goal in awaiting_goal_type with a linked direction
    direction_id = "015-test-goal-type"
    direction_dir = tmp_path / direction_id
    direction_dir.mkdir(parents=True)
    state_content = (
        "status: pr_open\n"
        "created_at: 2026-05-20T10:00:00+00:00\n"
        f"direction_id: {direction_id}\n"
        "pr_url: https://github.com/xvanov/sacrifice/pull/47\n"
        "summary: Dev iterating on tests.\n"
    )
    (direction_dir / "state.yaml").write_text(state_content)

    async with make_client() as client:
        token, user = await _auth(client)

        # Create a goal in awaiting_goal_type via DB
        engine = create_async_engine(settings.database_url, echo=False)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sf() as db:
            await db.execute(
                text("""
                    INSERT INTO goals
                        (id, user_id, title, description, goal_type, pledge_amount,
                         currency, deadline, timezone, recurrence, status,
                         awaiting_direction_id, charity_id, created_at, updated_at)
                    VALUES
                        (:id, :user_id, :title, :desc, :gtype, :amt,
                         :cur, :dl, :tz, :rec, :status,
                         :did, :cid, now(), now())
                """),
                {
                    "id": uuid.uuid4(),
                    "user_id": user["id"],
                    "title": "Test goal",
                    "desc": "test",
                    "gtype": "youtube_video",
                    "amt": 1000,
                    "cur": "usd",
                    "dl": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    "tz": "UTC",
                    "rec": "none",
                    "status": "awaiting_goal_type",
                    "did": direction_id,
                    "cid": None,
                },
            )
            await db.commit()
        await engine.dispose()

        with patch(
            "app.routes.chat.settings.directions_output_path", str(tmp_path)
        ):
            resp = await client.get(
                f"/api/chat/sessions/{session_id}/generation-status",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["direction_id"] == direction_id
    assert body["status"] == "pr_open"
    assert body["pr_url"] == "https://github.com/xvanov/sacrifice/pull/47"
    assert body["summary"] == "Dev iterating on tests."


async def test_generation_status_returns_404_when_no_generation():
    """generation-status must return 404 when user has no in-flight generation."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, _ = await _auth(client)

        # No goal in awaiting_goal_type for this user — should 404
        resp = await client.get(
            f"/api/chat/sessions/{session_id}/generation-status",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 404


@pytest.mark.parametrize("state_status,expected_api_status", [
    ("queued", "queued"),
    ("in_progress", "in_progress"),
    ("pr_open", "pr_open"),
    ("pr_merged", "pr_merged"),
    ("rejected", "rejected"),
])
async def test_generation_status_maps_lifecycle_states(state_status, expected_api_status, tmp_path):
    """generation-status must map each state.yaml status value to the contract response."""
    session_id = _make_session_id()

    direction_id = "016-lifecycle-test"
    direction_dir = tmp_path / direction_id
    direction_dir.mkdir(parents=True)
    state_content = (
        f"status: {state_status}\n"
        "created_at: 2026-05-20T10:00:00+00:00\n"
        f"direction_id: {direction_id}\n"
        "pr_url: null\n"
        "summary: ''\n"
    )
    (direction_dir / "state.yaml").write_text(state_content)

    async with make_client() as client:
        token, user = await _auth(client)

        engine = create_async_engine(settings.database_url, echo=False)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sf() as db:
            await db.execute(
                text("""
                    INSERT INTO goals
                        (id, user_id, title, description, goal_type, pledge_amount,
                         currency, deadline, timezone, recurrence, status,
                         awaiting_direction_id, charity_id, created_at, updated_at)
                    VALUES
                        (:id, :user_id, :title, :desc, :gtype, :amt,
                         :cur, :dl, :tz, :rec, :status,
                         :did, :cid, now(), now())
                """),
                {
                    "id": uuid.uuid4(),
                    "user_id": user["id"],
                    "title": "Test",
                    "desc": "test",
                    "gtype": "youtube_video",
                    "amt": 1000,
                    "cur": "usd",
                    "dl": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    "tz": "UTC",
                    "rec": "none",
                    "status": "awaiting_goal_type",
                    "did": direction_id,
                    "cid": None,
                },
            )
            await db.commit()
        await engine.dispose()

        with patch(
            "app.routes.chat.settings.directions_output_path", str(tmp_path)
        ):
            resp = await client.get(
                f"/api/chat/sessions/{session_id}/generation-status",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == expected_api_status


async def test_generation_status_defaults_queued_when_no_state_yaml(tmp_path):
    """generation-status must return 'queued' when direction dir exists but no state.yaml yet."""
    session_id = _make_session_id()

    direction_id = "017-no-state-yet"
    direction_dir = tmp_path / direction_id
    direction_dir.mkdir(parents=True)
    # No state.yaml written — just direction.md

    async with make_client() as client:
        token, user = await _auth(client)

        engine = create_async_engine(settings.database_url, echo=False)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sf() as db:
            await db.execute(
                text("""
                    INSERT INTO goals
                        (id, user_id, title, description, goal_type, pledge_amount,
                         currency, deadline, timezone, recurrence, status,
                         awaiting_direction_id, charity_id, created_at, updated_at)
                    VALUES
                        (:id, :user_id, :title, :desc, :gtype, :amt,
                         :cur, :dl, :tz, :rec, :status,
                         :did, :cid, now(), now())
                """),
                {
                    "id": uuid.uuid4(),
                    "user_id": user["id"],
                    "title": "Test",
                    "desc": "test",
                    "gtype": "youtube_video",
                    "amt": 1000,
                    "cur": "usd",
                    "dl": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    "tz": "UTC",
                    "rec": "none",
                    "status": "awaiting_goal_type",
                    "did": direction_id,
                    "cid": None,
                },
            )
            await db.commit()
        await engine.dispose()

        with patch(
            "app.routes.chat.settings.directions_output_path", str(tmp_path)
        ):
            resp = await client.get(
                f"/api/chat/sessions/{session_id}/generation-status",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["direction_id"] == direction_id


# ─── POST /api/chat/sessions/{session_id}/accept-generated-type ───────


async def test_accept_generated_type_returns_200_on_success(tmp_path):
    """accept-generated-type must transition goal to active when generation is merged."""
    session_id = _make_session_id()

    direction_id = "018-merged-direction"
    direction_dir = tmp_path / direction_id
    direction_dir.mkdir(parents=True)
    state_content = (
        "status: pr_merged\n"
        "created_at: 2026-05-20T10:00:00+00:00\n"
        f"direction_id: {direction_id}\n"
        "pr_url: https://github.com/xvanov/sacrifice/pull/48\n"
        "summary: Merged successfully.\n"
    )
    (direction_dir / "state.yaml").write_text(state_content)

    async with make_client() as client:
        token, user = await _auth(client)

        engine = create_async_engine(settings.database_url, echo=False)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        goal_id = None
        async with sf() as db:
            gid = uuid.uuid4()
            goal_id = str(gid)
            await db.execute(
                text("""
                    INSERT INTO goals
                        (id, user_id, title, description, goal_type, pledge_amount,
                         currency, deadline, timezone, recurrence, status,
                         awaiting_direction_id, charity_id, created_at, updated_at)
                    VALUES
                        (:id, :user_id, :title, :desc, :gtype, :amt,
                         :cur, :dl, :tz, :rec, :status,
                         :did, :cid, now(), now())
                """),
                {
                    "id": gid,
                    "user_id": user["id"],
                    "title": "Test goal",
                    "desc": "test",
                    "gtype": "youtube_video",
                    "amt": 1000,
                    "cur": "usd",
                    "dl": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    "tz": "UTC",
                    "rec": "none",
                    "status": "awaiting_goal_type",
                    "did": direction_id,
                    "cid": None,
                },
            )
            await db.commit()
        await engine.dispose()

        with patch(
            "app.routes.chat.settings.directions_output_path", str(tmp_path)
        ):
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/accept-generated-type",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["goal_id"] == goal_id
        assert body["status"] == "active"

        # Verify DB transition actually persisted
        engine2 = create_async_engine(settings.database_url, echo=False)
        sf2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
        async with sf2() as db:
            result = await db.execute(
                text("SELECT status FROM goals WHERE id = :id"),
                {"id": goal_id},
            )
            assert result.scalar() == "active"
        await engine2.dispose()


async def test_accept_generated_type_returns_409_when_not_merged(tmp_path):
    """accept-generated-type must return 409 when generation status != pr_merged."""
    session_id = _make_session_id()

    direction_id = "019-not-merged"
    direction_dir = tmp_path / direction_id
    direction_dir.mkdir(parents=True)
    state_content = (
        "status: in_progress\n"
        "created_at: 2026-05-20T10:00:00+00:00\n"
        f"direction_id: {direction_id}\n"
    )
    (direction_dir / "state.yaml").write_text(state_content)

    async with make_client() as client:
        token, user = await _auth(client)

        engine = create_async_engine(settings.database_url, echo=False)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sf() as db:
            await db.execute(
                text("""
                    INSERT INTO goals
                        (id, user_id, title, description, goal_type, pledge_amount,
                         currency, deadline, timezone, recurrence, status,
                         awaiting_direction_id, charity_id, created_at, updated_at)
                    VALUES
                        (:id, :user_id, :title, :desc, :gtype, :amt,
                         :cur, :dl, :tz, :rec, :status,
                         :did, :cid, now(), now())
                """),
                {
                    "id": uuid.uuid4(),
                    "user_id": user["id"],
                    "title": "Test",
                    "desc": "test",
                    "gtype": "youtube_video",
                    "amt": 1000,
                    "cur": "usd",
                    "dl": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    "tz": "UTC",
                    "rec": "none",
                    "status": "awaiting_goal_type",
                    "did": direction_id,
                    "cid": None,
                },
            )
            await db.commit()
        await engine.dispose()

        with patch(
            "app.routes.chat.settings.directions_output_path", str(tmp_path)
        ):
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/accept-generated-type",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 409


# ─── POST /api/chat/sessions/{session_id}/iterate-generated-type ──────


async def test_iterate_generated_type_returns_202_on_success(tmp_path):
    """iterate-generated-type must write a new direction with parent_direction frontmatter."""
    session_id = _make_session_id()

    previous_direction_id = "020-original-direction"
    previous_dir = tmp_path / previous_direction_id
    previous_dir.mkdir(parents=True)
    (previous_dir / "state.yaml").write_text(
        "status: pr_merged\n"
        "created_at: 2026-05-20T10:00:00+00:00\n"
        f"direction_id: {previous_direction_id}\n"
    )

    mock_llm_response = """---
title: Pushup Counter Side Angle
type: feature
parent_direction: 020-original-direction
why: This iterates on 020-original-direction to use side-on camera angle
acceptance: |
  modify the existing `backend/app/goal_types/pushup_counter/` module to
  Use a side-on camera angle instead of front-on; count partial reps as 0.5
"""

    async with make_client() as client:
        token, user = await _auth(client)

        engine = create_async_engine(settings.database_url, echo=False)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sf() as db:
            await db.execute(
                text("""
                    INSERT INTO goals
                        (id, user_id, title, description, goal_type, pledge_amount,
                         currency, deadline, timezone, recurrence, status,
                         awaiting_direction_id, charity_id, created_at, updated_at)
                    VALUES
                        (:id, :user_id, :title, :desc, :gtype, :amt,
                         :cur, :dl, :tz, :rec, :status,
                         :did, :cid, now(), now())
                """),
                {
                    "id": uuid.uuid4(),
                    "user_id": user["id"],
                    "title": "Test goal",
                    "desc": "test",
                    "gtype": "youtube_video",
                    "amt": 1000,
                    "cur": "usd",
                    "dl": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    "tz": "UTC",
                    "rec": "none",
                    "status": "awaiting_goal_type",
                    "did": previous_direction_id,
                    "cid": None,
                },
            )
            await db.commit()
        await engine.dispose()

        with patch(
            "app.routes.chat.settings.directions_output_path", str(tmp_path)
        ):
            mock_llm = AsyncMock(return_value=mock_llm_response)

            with patch.object(
                _direction_synth, "_call_llm", mock_llm
            ):
                resp = await client.post(
                    f"/api/chat/sessions/{session_id}/iterate-generated-type",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"feedback": "Use a side-on camera angle; count partial reps as 0.5."},
                )

        assert resp.status_code == 202
        body = resp.json()
        assert "direction_id" in body
        assert "previous_direction_id" in body
        assert body["previous_direction_id"] == previous_direction_id
        assert body["status"] == "queued"

        new_direction_id = body["direction_id"]
        new_dir = tmp_path / new_direction_id
        assert new_dir.is_dir()

        # Assert parent_direction frontmatter is in the written file
        md_text = (new_dir / "direction.md").read_text()
        assert "parent_direction:" in md_text
        assert previous_direction_id in md_text

        # Assert why prose references previous id-slug
        assert f"This iterates on {previous_direction_id}" in md_text

        # Assert acceptance includes user feedback verbatim
        assert "side-on camera angle" in md_text
        assert "count partial reps as 0.5" in md_text

        # Assert state.yaml exists
        assert (new_dir / "state.yaml").is_file()

        # Assert goal still in awaiting_goal_type (not accepted)
        engine2 = create_async_engine(settings.database_url, echo=False)
        sf2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
        async with sf2() as db:
            result = await db.execute(
                text("""
                    SELECT status, awaiting_direction_id FROM goals
                    WHERE user_id = :uid AND status = 'awaiting_goal_type'
                """),
                {"uid": user["id"]},
            )
            row = result.one_or_none()
            assert row is not None
            # awaiting_direction_id should be relinked to new direction
            assert row[1] == new_direction_id
        await engine2.dispose()

        # Assert ledger recorded (direction_id embedded in call_description)
        engine3 = create_async_engine(settings.database_url, echo=False)
        sf3 = async_sessionmaker(engine3, class_=AsyncSession, expire_on_commit=False)
        async with sf3() as db:
            result = await db.execute(
                text("SELECT COUNT(*) FROM chat_spend_ledger WHERE call_description LIKE :pat"),
                {"pat": f"%:{new_direction_id}"},
            )
            assert result.scalar() == 1
        await engine3.dispose()


async def test_iterate_generated_type_rejects_empty_feedback():
    """iterate-generated-type must return 422 for empty/whitespace feedback."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, _ = await _auth(client)

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "   "},
        )

        assert resp.status_code == 422


async def test_iterate_generated_type_returns_409_when_already_accepted():
    """iterate-generated-type must return 409 if goal is already active (accepted)."""
    session_id = _make_session_id()

    async with make_client() as client:
        token, user = await _auth(client)

        # Create an active goal (already accepted)
        engine = create_async_engine(settings.database_url, echo=False)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sf() as db:
            await db.execute(
                text("""
                    INSERT INTO goals
                        (id, user_id, title, description, goal_type, pledge_amount,
                         currency, deadline, timezone, recurrence, status,
                         awaiting_direction_id, charity_id, created_at, updated_at)
                    VALUES
                        (:id, :user_id, :title, :desc, :gtype, :amt,
                         :cur, :dl, :tz, :rec, :status,
                         :did, :cid, now(), now())
                """),
                {
                    "id": uuid.uuid4(),
                    "user_id": user["id"],
                    "title": "Already accepted goal",
                    "desc": "test",
                    "gtype": "youtube_video",
                    "amt": 1000,
                    "cur": "usd",
                    "dl": datetime(2026, 6, 1, tzinfo=timezone.utc),
                    "tz": "UTC",
                    "rec": "none",
                    "status": "active",  # already accepted
                    "did": "some-old-direction",
                    "cid": None,
                },
            )
            await db.commit()
        await engine.dispose()

        resp = await client.post(
            f"/api/chat/sessions/{session_id}/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "Change the camera angle"},
        )

        assert resp.status_code == 409
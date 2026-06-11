"""Tests for awaiting_goal_type status, direction linkage, and chat-generation endpoints.

These tests assert on production code that does NOT exist yet or contains
known bugs. Every test in this file MUST fail (RED) on first run.

Covers story acceptance criteria:
- awaiting_goal_type in Goal.status enum with nullable awaiting_direction_id
- POST /api/chat/sessions/{session_id}/request-new-goal-type creates goal + direction
- GET /api/goals/{id} exposes awaiting_direction_id populated by generation endpoint
- POST /api/chat/sessions/{session_id}/accept-generated-type transitions to active / 409
- GET /api/chat/sessions/{session_id}/generation-status maps to coarse API statuses
- Deadline worker skips awaiting_goal_type goals, processes active expired goals
- goal_type_ready notification emitted on pr_merged
- 409 response includes structured direction_id field
- iterate-generated-type rejects already-accepted goals
- YAML state parsing handles URLs in pr_url values
"""

import os
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models.goal import Goal
from app.models.notification import Notification


# ── Helpers ─────────────────────────────────────────────────────────────


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
    "title": "Ship the MVP",
    "description": "Launch the sacrifice app",
    "deadline": "2026-06-01T00:00:00Z",
    "pledge_amount": 5000,
    "goal_type": "youtube_video",
    "criteria": {"min_duration_seconds": 300, "video_description": "A walkthrough demo"},
    "charity_id": "acct_charity123",
}

GENERATION_REQUEST_BODY = {
    "prompt_summary": "Do 20 pushups every morning at 7am verified with my phone camera",
    "goal_payload_draft": {
        "title": "20 morning pushups",
        "description": "Do 20 pushups every morning at 7am, verified with my phone camera.",
        "pledge_amount": 1000,
        "currency": "usd",
        "deadline": "2026-05-26T11:00:00Z",
        "timezone": "America/New_York",
        "charity_id": None,
        "recurrence": "daily",
    },
}


def _fake_synthesis(prompt_summary="", chat_history=None):
    """Deterministic fake synthesis for tests — never calls an external LLM."""
    slug = "pushup-counter"
    title = "Pushup Counter"
    direction_md = f"""---
title: "{title}"
type: feature
why: "User requested verification for: {prompt_summary}"
acceptance:
  - "Create backend/app/goal_types/{slug}/ module conforming to the goal-type plugin base"
  - "Verifier accepts proof uploads and criteria_data payload"
  - "All fixture-based assertions pass"
---

# {title}

## Why
User needs a custom goal type for: {prompt_summary}

## Acceptance Criteria
1. Module created at `backend/app/goal_types/{slug}/`
2. Verifier correctly evaluates proof submissions
3. Tests pass with provided fixtures
"""
    return {
        "title": title,
        "slug": slug,
        "direction_md": direction_md,
        "flow_md": "# User flow\n\n1. Create goal\n2. Submit proof\n3. Verifier runs\n",
        "api_spec_md": "# API spec\n\nExisting endpoints apply.\n",
    }


@pytest.fixture
def temp_directions_path():
    """Override settings.directions_path with a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        original = settings.directions_path
        settings.directions_path = tmpdir
        yield Path(tmpdir)
        settings.directions_path = original


def _write_state_yaml(directions_root: Path, direction_id: str, status: str,
                      pr_url: str | None = None, summary: str | None = None):
    """Write a state.yaml for a direction, creating the directory if needed."""
    direction_dir = directions_root / direction_id
    direction_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"status: {status}"]
    if pr_url:
        lines.append(f"pr_url: {pr_url}")
    else:
        lines.append("pr_url: null")
    if summary:
        lines.append(f"summary: {summary}")
    else:
        lines.append(f"summary: Direction is {status}.")
    (direction_dir / "state.yaml").write_text("\n".join(lines) + "\n")


# Async wrapper so the mock can be used with AsyncMock if needed.
# For now patch the module-level symbol so all callers get the fake.
@pytest.fixture(autouse=True)
def mock_synthesize_direction():
    """Globally mock synthesize_direction so no test hits a real LLM."""
    with patch("app.routes.chat.synthesize_direction", side_effect=_fake_synthesis):
        yield


# ── Model-layer: persistence ────────────────────────────────────────────


async def test_model_persists_awaiting_goal_type_with_direction_id():
    """Goal model persists awaiting_goal_type status with awaiting_direction_id
    and correctly re-reads both fields."""
    engine = create_async_engine(settings.database_url, echo=False)
    from app.models.base import Base
    from app.models.user import User

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        user = User(
            email="persist@test.com",
            display_name="Persist Tester",
            auth_provider="google",
            auth_provider_id="google-persist-1",
        )
        session.add(user)
        await session.commit()

        goal = Goal(
            user_id=user.id,
            title="Awaiting goal type test",
            goal_type="youtube_video",
            pledge_amount=1000,
            deadline=datetime.now(timezone.utc) + timedelta(days=30),
            status="awaiting_goal_type",
            awaiting_direction_id="011-pushup-counter",
        )
        session.add(goal)
        await session.commit()
        goal_id = goal.id
        persisted_user_id = user.id  # capture scalar before leaving session

    # Re-read and verify both status and direction linkage persisted
    async with async_session() as session:
        result = await session.execute(select(Goal).where(Goal.id == goal_id))
        persisted = result.scalar_one()
        assert persisted.status == "awaiting_goal_type"
        assert persisted.awaiting_direction_id == "011-pushup-counter"

    # Verify null awaiting_direction_id also persists correctly
    async with async_session() as session:
        goal_null = Goal(
            user_id=persisted_user_id,
            title="Null direction linkage",
            goal_type="youtube_video",
            pledge_amount=1000,
            deadline=datetime.now(timezone.utc) + timedelta(days=30),
            status="awaiting_goal_type",
            awaiting_direction_id=None,
        )
        session.add(goal_null)
        await session.commit()
        null_id = goal_null.id

    async with async_session() as session:
        result = await session.execute(select(Goal).where(Goal.id == null_id))
        persisted_null = result.scalar_one()
        assert persisted_null.status == "awaiting_goal_type"
        assert persisted_null.awaiting_direction_id is None

    await engine.dispose()


# ── Generation endpoint: request-new-goal-type ──────────────────────────


async def test_request_new_goal_type_creates_goal_in_awaiting_status(temp_directions_path):
    """POST /api/chat/sessions/{id}/request-new-goal-type must create a goal
    in awaiting_goal_type status with awaiting_direction_id populated, write
    a direction directory, and return direction_id + goal_id."""
    async with make_client() as client:
        token, _ = await _auth(client)

        resp = await client.post(
            "/api/chat/sessions/sess-abc/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        body = resp.json()
        assert "direction_id" in body
        assert "goal_id" in body
        assert body["status"] == "queued"

        direction_id = body["direction_id"]
        goal_id = body["goal_id"]

        # Verify the direction directory was written
        direction_dir = temp_directions_path / direction_id
        assert direction_dir.is_dir()
        assert (direction_dir / "direction.md").exists()
        assert (direction_dir / "state.yaml").exists()

        # Verify state.yaml created with queued status
        state_content = (direction_dir / "state.yaml").read_text()
        assert "status: queued" in state_content

    # Verify goal persisted in DB with correct status and direction linkage.
    # Use a fresh engine/session to confirm the data was committed (not just
    # visible in the same transaction).
    engine = create_async_engine(settings.database_url, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(select(Goal).where(Goal.id == uuid.UUID(goal_id)))
        goal = result.scalar_one()
        assert goal.status == "awaiting_goal_type"
        assert goal.awaiting_direction_id == direction_id
    await engine.dispose()


async def test_goal_get_exposes_awaiting_direction_id_from_generation(temp_directions_path):
    """GET /api/goals/{id} must expose awaiting_direction_id populated by the
    generation endpoint, not by manual DB manipulation."""
    async with make_client() as client:
        token, _ = await _auth(client)

        # Create through the generation endpoint (the production path)
        resp = await client.post(
            "/api/chat/sessions/sess-def/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        goal_id = resp.json()["goal_id"]
        expected_direction_id = resp.json()["direction_id"]

        # GET the goal — awaiting_direction_id must be present
        resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["awaiting_direction_id"] == expected_direction_id
        assert body["status"] == "awaiting_goal_type"


async def test_normal_goal_has_null_awaiting_direction_id():
    """Goal created via POST /api/goals (not generation) must have
    awaiting_direction_id: null in the response."""
    async with make_client() as client:
        token, _ = await _auth(client)
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json=VALID_GOAL,
        )
        assert resp.status_code == 201
        goal_id = resp.json()["id"]

        resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "awaiting_direction_id" in body
        assert body["awaiting_direction_id"] is None


# ── Accept endpoint ─────────────────────────────────────────────────────


async def test_accept_generated_type_transitions_to_active(temp_directions_path):
    """POST /api/chat/sessions/{id}/accept-generated-type must transition
    the goal from awaiting_goal_type to active when state is pr_merged."""
    async with make_client() as client:
        token, _ = await _auth(client)

        # Create goal via generation endpoint
        resp = await client.post(
            "/api/chat/sessions/sess-ghi/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        goal_id = resp.json()["goal_id"]
        direction_id = resp.json()["direction_id"]

        # Write pr_merged state so accept is allowed
        _write_state_yaml(temp_directions_path, direction_id, "pr_merged",
                          pr_url="https://github.com/xvanov/sacrifice/pull/47")

        # Accept the generated type
        resp = await client.post(
            "/api/chat/sessions/sess-ghi/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["goal_id"] == goal_id
        assert body["status"] == "active"

        # Verify goal is now active via GET
        resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"


async def test_accept_generated_type_returns_409_when_not_merged(temp_directions_path):
    """POST /api/chat/sessions/{id}/accept-generated-type must return 409
    when the direction state is not pr_merged."""
    async with make_client() as client:
        token, _ = await _auth(client)

        resp = await client.post(
            "/api/chat/sessions/sess-jkl/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        direction_id = resp.json()["direction_id"]

        # Write queued state — NOT merged
        _write_state_yaml(temp_directions_path, direction_id, "queued")

        resp = await client.post(
            "/api/chat/sessions/sess-jkl/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409

        # Also test with in_progress state
        _write_state_yaml(temp_directions_path, direction_id, "in_progress")

        resp = await client.post(
            "/api/chat/sessions/sess-jkl/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409

        # Also test with pr_open state
        _write_state_yaml(temp_directions_path, direction_id, "pr_open",
                          pr_url="https://github.com/xvanov/sacrifice/pull/47")

        resp = await client.post(
            "/api/chat/sessions/sess-jkl/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409


# ── Generation status endpoint ──────────────────────────────────────────


async def test_generation_status_maps_to_coarse_api_statuses(temp_directions_path):
    """GET /api/chat/sessions/{id}/generation-status must return coarse
    API statuses (queued|in_progress|pr_open|pr_merged|rejected), not raw
    factory lifecycle states like 'merging'."""
    async with make_client() as client:
        token, _ = await _auth(client)

        # Create goal via generation
        resp = await client.post(
            "/api/chat/sessions/sess-mno/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        direction_id = resp.json()["direction_id"]

        # The raw factory lifecycle includes states like 'merging' that are
        # NOT in the coarse API set. Write such a state and verify the
        # endpoint maps it to a valid coarse status.
        _write_state_yaml(temp_directions_path, direction_id, "merging",
                          pr_url="https://github.com/xvanov/sacrifice/pull/47",
                          summary="PR is being merged.")

        resp = await client.get(
            "/api/chat/sessions/sess-mno/generation-status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["direction_id"] == direction_id
        # Must be one of the five coarse API statuses
        assert body["status"] in {"queued", "in_progress", "pr_open", "pr_merged", "rejected"}, \
            f"Expected coarse status, got raw: {body['status']}"


async def test_generation_status_handles_urls_in_state_yaml(temp_directions_path):
    """GET /api/chat/sessions/{id}/generation-status must correctly parse
    state.yaml even when pr_url contains colons (https://...)."""
    async with make_client() as client:
        token, _ = await _auth(client)

        resp = await client.post(
            "/api/chat/sessions/sess-pqr/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        direction_id = resp.json()["direction_id"]

        # Write a state.yaml with a real GitHub PR URL (contains : after https)
        _write_state_yaml(temp_directions_path, direction_id, "pr_open",
                          pr_url="https://github.com/xvanov/sacrifice/pull/47",
                          summary="PR is open for review.")

        resp = await client.get(
            "/api/chat/sessions/sess-pqr/generation-status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pr_url"] == "https://github.com/xvanov/sacrifice/pull/47"
        assert body["status"] == "pr_open"


# ── Deadline worker ─────────────────────────────────────────────────────


async def test_deadline_worker_skips_awaiting_goal_type_processes_active(temp_directions_path):
    """check_deadlines must skip awaiting_goal_type goals (no charge, no status
    change) while still processing active expired goals. Assert on real
    persisted goal status side effects."""
    from app.workers.deadline import check_deadlines

    past_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    async with make_client() as client:
        token, _ = await _auth(client)

        # Create an awaiting_goal_type goal with past deadline via generation
        resp = await client.post(
            "/api/chat/sessions/sess-stu/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={
                **GENERATION_REQUEST_BODY,
                "goal_payload_draft": {
                    **GENERATION_REQUEST_BODY["goal_payload_draft"],
                    "deadline": past_deadline,
                },
            },
        )
        assert resp.status_code == 202
        awaiting_goal_id = uuid.UUID(resp.json()["goal_id"])

        # Create an active goal with past deadline via normal endpoint + status change
        resp = await client.post(
            "/api/goals",
            headers={"Authorization": f"Bearer {token}"},
            json={**VALID_GOAL, "deadline": past_deadline},
        )
        active_goal_id = uuid.UUID(resp.json()["id"])
        # Transition to active
        resp = await client.put(
            f"/api/goals/{active_goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "active"},
        )
        assert resp.status_code == 200

        # Run the deadline worker — mock Stripe payment so it doesn't hang
        with patch("app.workers.deadline.process_charge_for_goal") as mock_charge:
            await check_deadlines()

        # Assert: awaiting_goal_type goal is untouched
        engine = create_async_engine(settings.database_url, echo=False)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            result = await session.execute(select(Goal).where(Goal.id == awaiting_goal_id))
            awaiting_goal = result.scalar_one()
            assert awaiting_goal.status == "awaiting_goal_type", \
                "awaiting_goal_type goal must not be failed by deadline worker"

            # Assert: active expired goal WAS processed (failed)
            result = await session.execute(select(Goal).where(Goal.id == active_goal_id))
            active_goal = result.scalar_one()
            assert active_goal.status == "failed", \
                "active expired goal must be failed by deadline worker"

            # Assert: charge was only attempted for the active goal
            mock_charge.assert_called_once()
            call_args = mock_charge.call_args[0]
            assert str(active_goal_id) in call_args, \
                "charge must be for the active goal, not the awaiting one"
        await engine.dispose()


# ─── Notification: goal_type_ready ──────────────────────────────────────


async def test_notification_emitted_on_pr_merged(temp_directions_path):
    """When generation-status is polled and state is pr_merged, a
    goal_type_ready notification must be persisted for the correct goal.
    A second poll must NOT create a duplicate notification."""
    async with make_client() as client:
        token, _ = await _auth(client)

        resp = await client.post(
            "/api/chat/sessions/sess-vwx/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        goal_id = resp.json()["goal_id"]
        direction_id = resp.json()["direction_id"]

        # Write pr_merged state
        _write_state_yaml(temp_directions_path, direction_id, "pr_merged",
                          pr_url="https://github.com/xvanov/sacrifice/pull/47")

        # Poll generation-status — this must trigger notification creation
        resp = await client.get(
            "/api/chat/sessions/sess-vwx/generation-status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Verify notification persisted in DB
        engine = create_async_engine(settings.database_url, echo=False)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            result = await session.execute(
                select(Notification).where(
                    Notification.type == "goal_type_ready",
                )
            )
            notifs = list(result.scalars().all())
            assert len(notifs) >= 1, "goal_type_ready notification must be created"
            matching = [n for n in notifs if str(n.goal_id) == goal_id]
            assert len(matching) == 1, \
                "notification must reference the correct goal"

        # Second poll must NOT create a duplicate
        resp = await client.get(
            "/api/chat/sessions/sess-vwx/generation-status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        async with async_session() as session:
            result = await session.execute(
                select(Notification).where(
                    Notification.type == "goal_type_ready",
                )
            )
            notifs = list(result.scalars().all())
            matching = [n for n in notifs if str(n.goal_id) == goal_id]
            assert len(matching) == 1, \
                "duplicate goal_type_ready notification must not be created"
        await engine.dispose()


# ── 409 response structure ──────────────────────────────────────────────


async def test_request_new_goal_type_409_includes_structured_direction_id(temp_directions_path):
    """When a user already has an in-flight generation, the 409 response
    must include the existing direction_id as a structured JSON field,
    not buried inside a free-text detail string."""
    async with make_client() as client:
        token, _ = await _auth(client)

        # First request — succeeds
        resp = await client.post(
            "/api/chat/sessions/sess-yza/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        first_direction_id = resp.json()["direction_id"]

        # Second request — must be 409 with structured direction_id
        resp = await client.post(
            "/api/chat/sessions/sess-yza/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={
                **GENERATION_REQUEST_BODY,
                "prompt_summary": "A different goal type entirely",
            },
        )
        assert resp.status_code == 409
        body = resp.json()
        assert "direction_id" in body, \
            "409 response must include direction_id as a structured field"
        assert body["direction_id"] == first_direction_id


# ── Iterate endpoint ────────────────────────────────────────────────────


async def test_iterate_generated_type_returns_409_when_already_accepted(temp_directions_path):
    """POST /api/chat/sessions/{id}/iterate-generated-type must return 409
    when the goal has already been accepted (not in awaiting_goal_type)."""
    async with make_client() as client:
        token, _ = await _auth(client)

        # Create goal via generation
        resp = await client.post(
            "/api/chat/sessions/sess-bcd/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        direction_id = resp.json()["direction_id"]

        # Accept it (write pr_merged state first)
        _write_state_yaml(temp_directions_path, direction_id, "pr_merged",
                          pr_url="https://github.com/xvanov/sacrifice/pull/47")
        resp = await client.post(
            "/api/chat/sessions/sess-bcd/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Now try to iterate — must be 409
        resp = await client.post(
            "/api/chat/sessions/sess-bcd/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "Use a side-on camera angle instead."},
        )
        assert resp.status_code == 409


async def test_iterate_generated_type_creates_new_direction_with_parent_linkage(temp_directions_path):
    """POST /api/chat/sessions/{id}/iterate-generated-type must create a new
    direction whose direction.md contains parent_direction frontmatter
    referencing the previous direction, and return both ids."""
    async with make_client() as client:
        token, _ = await _auth(client)

        # Create initial direction
        resp = await client.post(
            "/api/chat/sessions/sess-efg/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        previous_direction_id = resp.json()["direction_id"]

        # Iterate
        resp = await client.post(
            "/api/chat/sessions/sess-efg/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "Use a side-on camera angle; count partial reps as 0.5."},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert "direction_id" in body
        assert body["previous_direction_id"] == previous_direction_id
        assert body["status"] == "queued"

        new_direction_id = body["direction_id"]
        assert new_direction_id != previous_direction_id

        # Verify parent_direction in the new direction's direction.md
        direction_md = (temp_directions_path / new_direction_id / "direction.md").read_text()
        assert f"parent_direction: {previous_direction_id}" in direction_md


# ── Spend cap ───────────────────────────────────────────────────────────


async def test_request_new_goal_type_returns_429_when_spend_cap_exceeded(temp_directions_path):
    """POST /api/chat/sessions/{id}/request-new-goal-type must return 429
    when the user's daily spend cap is exceeded."""
    from app.models.chat_spend import ChatSpendLedger

    async with make_client() as client:
        token, user = await _auth(client)

        # Insert a spend ledger entry that exceeds the cap
        engine = create_async_engine(settings.database_url, echo=False)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            entry = ChatSpendLedger(
                user_id=uuid.UUID(user["id"]),
                cost_millicents=settings.chat_spend_cap_millicents + 1,
                model="test-model",
                call_description="prior spend exceeding cap",
            )
            session.add(entry)
            await session.commit()
        await engine.dispose()

        resp = await client.post(
            "/api/chat/sessions/sess-hij/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 429
        assert "budget" in resp.json()["detail"].lower()
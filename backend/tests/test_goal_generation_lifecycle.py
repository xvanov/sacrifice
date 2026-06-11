"""Lifecycle-focused tests for goal-generation chat flow.

Covers:
- POST /api/chat/sessions/{id}/accept-generated-type transitions to active
- POST /api/chat/sessions/{id}/accept-generated-type returns 409 when not merged
- GET /api/chat/sessions/{id}/generation-status maps to coarse API statuses
- GET /api/chat/sessions/{id}/generation-status suppresses notification for non-merged states
- Deadline worker skips awaiting_goal_type goals, processes active expired goals
- goal_type_ready notification emitted on pr_merged (idempotent)
- POST /api/chat/sessions/{id}/iterate-generated-type returns 409 when accepted
- POST /api/chat/sessions/{id}/iterate-generated-type creates new direction with parent linkage
- Iteration preserves canonical module_name in criteria_data
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.goal import Goal
from app.models.notification import Notification

from .utils_goal_generation import (
    GENERATION_REQUEST_BODY,
    VALID_GOAL,
    _auth,
    _ensure_session,
    _write_state_yaml,
    make_client,
    temp_directions_path,
    mock_synthesize_direction,
)


TEST_PLAN = {
    "test_accept_generated_type_transitions_to_active": (
        "AC: POST accept must transition goal from awaiting_goal_type to active "
        "when state is pr_merged and the module is registered. Verifies goal_type "
        "is set to the concrete module name (not __generated__), direction linkage "
        "cleared, and criteria_data preserves canonical module_name."
    ),
    "test_accept_generated_type_returns_409_when_not_merged": (
        "AC: API spec 409 when direction state != pr_merged. Tests queued, "
        "in_progress, and pr_open states all reject acceptance."
    ),
    "test_accept_generated_type_returns_409_for_unresolved_module": (
        "CR1: Accept must return 409 when the module is not registered in "
        "the in-memory registry — the factory chain merge migration hasn't "
        "completed. Verifies the endpoint does not activate a non-dispatchable goal."
    ),
    "test_generation_status_maps_to_coarse_api_statuses": (
        "AC: GET generation-status maps raw factory states (like 'merging') "
        "to coarse API statuses in {queued, in_progress, pr_open, pr_merged, rejected}. "
        "Also verifies pr_url with colons survives yaml.safe_load intact."
    ),
    "test_generation_status_suppresses_notification_for_non_merged": (
        "AC: GET generation-status must NOT fire goal_type_ready notification "
        "for non-merged states (pr_open). Exercises real endpoint and asserts no "
        "side-effect notification persisted in DB for pr_open."
    ),
    "test_deadline_worker_skips_awaiting_goal_type_processes_active": (
        "AC: check_deadlines must skip awaiting_goal_type goals (no charge, "
        "no status change) while processing active expired goals. Calls real worker."
    ),
    "test_notification_emitted_on_pr_merged": (
        "AC: On pr_merged, goal_type_ready notification persisted. Second poll "
        "must NOT duplicate. Exercises real generation-status endpoint and DB read-back."
    ),
    "test_iterate_generated_type_returns_409_when_already_accepted": (
        "AC: iterate must return 409 when goal already accepted. Exercises "
        "real request → accept (with registered type) → iterate flow."
    ),
    "test_iterate_generated_type_creates_new_direction_with_parent_linkage": (
        "AC: iterate creates new direction with parent_direction frontmatter, "
        "substantive slug (no iterate-N tokens), why prose referencing previous "
        "direction, and acceptance criteria with correct shape."
    ),
    "test_iterate_preserves_canonical_module_name": (
        "AC: Iteration must preserve the original module_name in criteria_data "
        "while updating direction_id. Verifies the iteration does NOT rename "
        "the target module."
    ),
    "test_accept_generated_type_is_dispatchable": (
        "CR1/TQ1: After accept, a goal is fully dispatchable — goal.goal_type "
        "is the concrete module name (not __generated__). The submit-proof route "
        "resolves the verifier from goal.goal_type directly, no fallback. Asserts "
        "202 with submission_id and persisted goal_type == pushup_counter."
    ),
}


# ── Accept endpoint ──────────────────────────────────────────────────────


async def test_accept_generated_type_transitions_to_active(temp_directions_path):
    """POST /api/chat/sessions/{id}/accept-generated-type must transition
    the goal from awaiting_goal_type to active when state is pr_merged.
    The goal.goal_type is set to the concrete module name (e.g. pushup_counter)
    so the goal is fully dispatchable — no __generated__ placeholder remains."""
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-ghi")

        resp = await client.post(
            "/api/chat/sessions/sess-ghi/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        goal_id = resp.json()["goal_id"]
        direction_id = resp.json()["direction_id"]

        _write_state_yaml(temp_directions_path, direction_id, "pr_merged",
                          pr_url="https://github.com/xvanov/sacrifice/pull/47")

        # Register the module in the in-memory registry so the accept
        # endpoint's pre-flight registry check passes. The factory chain's
        # merge migration would have added the type to the PG goal_type
        # enum; the accept endpoint sets goal.goal_type directly.
        from app.goal_types.registry import _DynamicGoalType
        _fake_gt = _DynamicGoalType(
            name="pushup_counter",
            description="Count pushups from video",
            sample_prompts=["Do 20 pushups"],
            criteria_schema={"type": "object", "properties": {"count": {"type": "integer"}}},
            verify=lambda pd, cd: {"status": "verified"},
        )
        import app.goal_types.registry as _reg
        _reg._registry["pushup_counter"] = _fake_gt
        try:
            resp = await client.post(
                "/api/chat/sessions/sess-ghi/accept-generated-type",
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            _reg._registry.pop("pushup_counter", None)

        assert resp.status_code == 200
        body = resp.json()
        assert body["goal_id"] == goal_id
        assert body["status"] == "active"

        # Verify goal is now active via GET, direction linkage is cleared,
        # and goal_type has been switched to the concrete module name.
        resp = await client.get(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "active"
        assert body["goal_type"] == "pushup_counter", \
            "goal_type must be the concrete module name, not __generated__"
        assert body["awaiting_direction_id"] is None
        assert body["criteria"] is not None, "accepted goal must retain criteria"
        assert body["criteria"]["criteria_type"] == "generated"
        assert "module_name" in body["criteria"]["criteria_data"]
        assert body["criteria"]["criteria_data"]["module_name"] == "pushup_counter"


@pytest.mark.parametrize("non_merged_status", ["queued", "in_progress", "pr_open"])
async def test_accept_generated_type_returns_409_when_not_merged(temp_directions_path, non_merged_status):
    """POST /api/chat/sessions/{id}/accept-generated-type must return 409
    for every non-merged direction state (queued, in_progress, pr_open)."""
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-jkl")

        resp = await client.post(
            "/api/chat/sessions/sess-jkl/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        direction_id = resp.json()["direction_id"]

        pr_url = "https://github.com/xvanov/sacrifice/pull/47" if non_merged_status == "pr_open" else None
        _write_state_yaml(temp_directions_path, direction_id, non_merged_status, pr_url=pr_url)

        resp = await client.post(
            "/api/chat/sessions/sess-jkl/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409
        assert "not yet merged" in resp.json()["detail"].lower()


async def test_accept_generated_type_returns_409_for_unresolved_module(temp_directions_path):
    """POST /api/chat/sessions/{id}/accept-generated-type must return 409
    when the generated module is not registered in the in-memory registry —
    the factory chain merge migration hasn't completed. The endpoint must
    not activate a non-dispatchable goal."""
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-unreg")

        resp = await client.post(
            "/api/chat/sessions/sess-unreg/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        direction_id = resp.json()["direction_id"]

        _write_state_yaml(temp_directions_path, direction_id, "pr_merged",
                          pr_url="https://github.com/xvanov/sacrifice/pull/47")

        # Do NOT register the module — simulate merge migration not yet run.
        resp = await client.post(
            "/api/chat/sessions/sess-unreg/accept-generated-type",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "not yet registered" in detail.lower()
        assert "pushup_counter" in detail


async def test_accept_generated_type_is_dispatchable(temp_directions_path):
    """After accept, a goal must be fully dispatchable — goal.goal_type is
    the concrete module name (e.g. pushup_counter), not __generated__.
    The submit-proof route resolves the verifier from goal.goal_type directly
    and calls verify() on the concrete type, returning 202 with a
    submission_id for background verification dispatch."""
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-ghi")

        resp = await client.post(
            "/api/chat/sessions/sess-ghi/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        goal_id = resp.json()["goal_id"]
        direction_id = resp.json()["direction_id"]

        _write_state_yaml(temp_directions_path, direction_id, "pr_merged",
                          pr_url="https://github.com/xvanov/sacrifice/pull/47")

        from app.goal_types.registry import _DynamicGoalType
        async def _fake_verify(proof_data, criteria_data):
            return {"status": "verified"}

        _fake_gt = _DynamicGoalType(
            name="pushup_counter",
            description="Count pushups from video",
            sample_prompts=["Do 20 pushups"],
            criteria_schema={"type": "object", "properties": {"count": {"type": "integer"}}},
            verify=_fake_verify,
        )
        import app.goal_types.registry as _reg
        _reg._registry["pushup_counter"] = _fake_gt
        try:
            resp = await client.post(
                "/api/chat/sessions/sess-ghi/accept-generated-type",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

            # Submit proof — the goal.goal_type is now pushup_counter,
            # no fallback needed. The submit-proof route calls verify()
            # on the resolved concrete type directly.
            resp = await client.post(
                f"/api/goals/{goal_id}/submit-proof",
                headers={"Authorization": f"Bearer {token}"},
                json={"youtube_url": "https://youtu.be/test12345"},
            )
            assert resp.status_code == 202, \
                f"submit proof should accept for background verification, got: {resp.json()}"
            body = resp.json()
            assert body["submission_id"] is not None, \
                "proof submission must produce a submission_id"
            assert body["verification_status"] == "pending", \
                "proof verification is dispatched asynchronously"

            # Verify the persisted goal has the concrete module name.
            resp = await client.get(
                f"/api/goals/{goal_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            goal_body = resp.json()
            assert goal_body["goal_type"] == "pushup_counter", \
                "goal_type must be the concrete module name, no fallback"
            assert goal_body["criteria"]["criteria_data"]["module_name"] == "pushup_counter"
        finally:
            _reg._registry.pop("pushup_counter", None)


# ── Generation status endpoint ───────────────────────────────────────────


async def test_generation_status_maps_to_coarse_api_statuses(temp_directions_path):
    """GET /api/chat/sessions/{id}/generation-status must map raw factory
    lifecycle states to coarse API statuses and correctly parse pr_url
    even when it contains colons (https://...). Specifically, the raw
    'merging' state must map to coarse 'pr_open'."""
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-mno")

        resp = await client.post(
            "/api/chat/sessions/sess-mno/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        direction_id = resp.json()["direction_id"]

        pr_url = "https://github.com/xvanov/sacrifice/pull/47"
        _write_state_yaml(temp_directions_path, direction_id, "merging",
                          pr_url=pr_url, summary="PR is being merged.")

        resp = await client.get(
            "/api/chat/sessions/sess-mno/generation-status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["direction_id"] == direction_id
        # The story requires raw 'merging' → coarse 'pr_open'
        assert body["status"] == "pr_open", \
            f"Expected raw 'merging' to map to coarse 'pr_open', got: {body['status']}"
        # URL with colons must survive yaml.safe_load intact
        assert body["pr_url"] == pr_url, \
            f"Expected pr_url {pr_url}, got: {body['pr_url']}"


async def test_generation_status_suppresses_notification_for_non_merged(temp_directions_path):
    """GET /api/chat/sessions/{id}/generation-status must NOT fire
    goal_type_ready notification for non-merged states (pr_open).
    Verifies the side-effect behavior that only pr_merged triggers
    notification creation."""
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-pqr")

        resp = await client.post(
            "/api/chat/sessions/sess-pqr/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        goal_id = resp.json()["goal_id"]
        direction_id = resp.json()["direction_id"]

        _write_state_yaml(temp_directions_path, direction_id, "pr_open",
                          pr_url="https://github.com/xvanov/sacrifice/pull/47",
                          summary="PR is open for review.")

        # Poll generation-status — must NOT create a goal_type_ready notification
        resp = await client.get(
            "/api/chat/sessions/sess-pqr/generation-status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pr_open"

        # Verify no goal_type_ready notification was persisted for non-merged state
        engine = create_async_engine(settings.database_url, echo=False)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            result = await session.execute(
                select(Notification).where(
                    Notification.goal_id == uuid.UUID(goal_id),
                    Notification.type == "goal_type_ready",
                )
            )
            notifs = list(result.scalars().all())
            assert len(notifs) == 0, \
                "goal_type_ready notification must NOT fire for non-merged status"
        await engine.dispose()


# ── Deadline worker ──────────────────────────────────────────────────────


async def test_deadline_worker_skips_awaiting_goal_type_processes_active(temp_directions_path):
    """check_deadlines must skip awaiting_goal_type goals (no charge, no status
    change) while still processing active expired goals. Assert on real
    persisted goal status side effects."""
    from app.workers.deadline import check_deadlines

    past_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-stu")

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


# ─── Notification: goal_type_ready ───────────────────────────────────────


async def test_notification_emitted_on_pr_merged(temp_directions_path):
    """When generation-status is polled and state is pr_merged, a
    goal_type_ready notification must be persisted for the correct goal.
    A second poll must NOT create a duplicate notification."""
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-vwx")

        resp = await client.post(
            "/api/chat/sessions/sess-vwx/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        goal_id = resp.json()["goal_id"]
        direction_id = resp.json()["direction_id"]

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


# ── Iterate endpoint ─────────────────────────────────────────────────────


async def test_iterate_generated_type_returns_409_when_already_accepted(temp_directions_path):
    """POST /api/chat/sessions/{id}/iterate-generated-type must return 409
    when the goal has already been accepted (not in awaiting_goal_type)."""
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-bcd")

        resp = await client.post(
            "/api/chat/sessions/sess-bcd/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        direction_id = resp.json()["direction_id"]

        _write_state_yaml(temp_directions_path, direction_id, "pr_merged",
                          pr_url="https://github.com/xvanov/sacrifice/pull/47")

        # Register in the in-memory registry so the accept endpoint can
        # verify the module exists (no DB enum mutation needed).
        from app.goal_types.registry import _DynamicGoalType
        _fake_gt = _DynamicGoalType(
            name="pushup_counter",
            description="Count pushups from video",
            sample_prompts=["Do 20 pushups"],
            criteria_schema={"type": "object", "properties": {"count": {"type": "integer"}}},
            verify=lambda pd, cd: {"status": "verified"},
        )
        import app.goal_types.registry as _reg
        _reg._registry["pushup_counter"] = _fake_gt
        try:
            resp = await client.post(
                "/api/chat/sessions/sess-bcd/accept-generated-type",
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            _reg._registry.pop("pushup_counter", None)

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
        await _ensure_session(client, "sess-efg")

        resp = await client.post(
            "/api/chat/sessions/sess-efg/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        previous_direction_id = resp.json()["direction_id"]

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

        # Verify the new direction follows the story's strict shape rules.
        direction_dir = temp_directions_path / new_direction_id
        assert direction_dir.is_dir()

        # 1. direction.md must contain parent_direction frontmatter
        direction_md = (direction_dir / "direction.md").read_text()
        assert f"parent_direction: {previous_direction_id}" in direction_md

        # 2. Slug must be substantive — the new direction_id embeds the slug
        #    after the numeric prefix. The slug must NOT contain iterate/
        #    iteration keywords or bare digits.
        slug_part = new_direction_id.split("-", 1)[1] if "-" in new_direction_id else ""
        forbidden = {"iterate", "iteration", "iter"}
        for token in slug_part.split("-"):
            assert token not in forbidden, \
                f"slug must not contain forbidden token '{token}': {new_direction_id}"
            assert not token.isdigit(), \
                f"slug must not contain bare digit '{token}': {new_direction_id}"
        # The slug should contain at least one feedback-derived word
        assert len(slug_part.split("-")) >= 2, \
            f"slug should contain feedback-derived words: {new_direction_id}"

        # 3. direction.md why prose must reference the previous direction
        assert f"iterates on {previous_direction_id}" in direction_md.lower()

        # 4. Acceptance criteria must use the exact shape from the story
        assert "modify the existing" in direction_md.lower()
        assert "backend/app/goal_types/" in direction_md
        assert "address the following feedback" in direction_md
        # user feedback must appear verbatim
        assert "side-on camera angle" in direction_md
        assert "count partial reps as 0.5" in direction_md


async def test_iterate_preserves_canonical_module_name(temp_directions_path):
    """POST /api/chat/sessions/{id}/iterate-generated-type must preserve
    the original module_name in criteria_data while updating direction_id."""
    async with make_client() as client:
        token, _ = await _auth(client)
        await _ensure_session(client, "sess-iter-modname")

        resp = await client.post(
            "/api/chat/sessions/sess-iter-modname/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json=GENERATION_REQUEST_BODY,
        )
        assert resp.status_code == 202
        goal_id = resp.json()["goal_id"]
        previous_direction_id = resp.json()["direction_id"]

        # Verify the original module_name is set
        engine = create_async_engine(settings.database_url, echo=False)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            from sqlalchemy.orm import selectinload
            result = await session.execute(
                select(Goal).options(selectinload(Goal.criteria)).where(Goal.id == uuid.UUID(goal_id))
            )
            goal = result.scalar_one()
            original_module_name = goal.criteria.criteria_data.get("module_name")
            assert original_module_name == "pushup_counter"
        await engine.dispose()

        # Iterate
        resp = await client.post(
            "/api/chat/sessions/sess-iter-modname/iterate-generated-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"feedback": "Use side angle; count partial reps as half."},
        )
        assert resp.status_code == 202
        new_direction_id = resp.json()["direction_id"]
        assert new_direction_id != previous_direction_id

        # Verify module_name is preserved, only direction_id changed
        engine = create_async_engine(settings.database_url, echo=False)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            from sqlalchemy.orm import selectinload
            result = await session.execute(
                select(Goal).options(selectinload(Goal.criteria)).where(Goal.id == uuid.UUID(goal_id))
            )
            goal = result.scalar_one()
            assert goal.awaiting_direction_id == new_direction_id
            assert goal.criteria.criteria_data["module_name"] == original_module_name, \
                "module_name must be preserved across iterations"
            assert goal.criteria.criteria_data["direction_id"] == new_direction_id, \
                "direction_id must be updated to the new iteration"
        await engine.dispose()
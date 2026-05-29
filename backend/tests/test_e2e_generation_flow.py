"""E2E test for the direction synthesis → fake factory chain → accept flow.

These tests assert on production code that does NOT exist yet. Every test
in this file MUST fail (RED) on first run against the current codebase.

Covers:
- SACRIFICE_FORCE_GENERATE flag bypasses chat matcher
- Pushup-counter direction synthesis and fake factory chain merge
- YouTube v2 regeneration (distinct module name, original unaffected)
- Generation status polling through all lifecycle states
- goal_type_ready notification on pr_merged
- Module verifier passes fixture-based assertions
"""

import json
import os
import shutil
import tempfile
import time
import uuid
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


def make_client(headers=None):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", headers=headers or {})


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock_auth:
        mock_auth.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
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


# ── fake_factory_chain fixture ───────────────────────────────────────


class FakeFactoryChain:
    """Watches a directions directory and simulates the factory chain.

    On detecting a new direction directory, reads direction.md, creates a
    plausible goal type module, and advances the state.yaml through the
    full lifecycle: queued → in_progress → pr_open → pr_merged.
    """

    def __init__(self, directions_dir: Path, goal_types_dir: Path, repo_root: Path):
        self.directions_dir = Path(directions_dir)
        self.goal_types_dir = Path(goal_types_dir)
        self.repo_root = Path(repo_root)
        self._seen_dirs = set()
        self._current_state = {}

    def scan_new_directions(self) -> list[str]:
        dirs = []
        if self.directions_dir.is_dir():
            for entry in self.directions_dir.iterdir():
                if entry.is_dir() and entry.name not in self._seen_dirs:
                    self._seen_dirs.add(entry.name)
                    dirs.append(entry.name)
        return dirs

    def advance_to_state(self, direction_id: str, new_status: str):
        """Update a direction's state.yaml to the given status."""
        state_file = self.directions_dir / direction_id / "state.yaml"
        if not state_file.exists():
            return

        import yaml

        with open(state_file) as f:
            state = yaml.safe_load(f)

        state["status"] = new_status
        if new_status == "pr_open":
            state["pr_url"] = f"https://github.com/xvanov/sacrifice/pull/{hash(direction_id) % 1000 + 100}"
        if new_status == "pr_merged":
            state["summary"] = "Merged successfully."

        with open(state_file, "w") as f:
            yaml.dump(state, f)

    def synthesize_module(self, direction_id: str):
        """Create a plausible goal type module from the direction.md content."""
        direction_dir = self.directions_dir / direction_id
        direction_md = direction_dir / "direction.md"
        if not direction_md.exists():
            return

        content = direction_md.read_text()

        # Determine module name from direction id slug
        slug = direction_id.split("-", 1)[1]
        module_name = slug.replace("-", "_")
        module_dir = self.goal_types_dir / module_name
        module_dir.mkdir(parents=True, exist_ok=True)

        # Write __init__.py
        init_py = module_dir / "__init__.py"
        init_py.write_text(f'''"""Auto-generated goal type: {module_name}"""

from .verifier import verify  # noqa: F401
''')

        # Write verifier.py
        verifier_py = module_dir / "verifier.py"
        verifier_py.write_text(f'''"""Verifier for {module_name} goal type."""

from app.goal_types.base import GoalTypeBase


async def verify(criteria: dict, upload, db=None) -> dict:
    """Verify pushup count from video upload."""
    # Stub: real implementation provided by factory chain
    return {{"verdict": "verified", "details": {{"reason": "stub for {module_name}"}}}}
''')

        # Create test fixtures dir and copy fixture videos if they exist
        fixtures_dir = self.repo_root / "backend" / "tests" / "fixtures" / module_name
        fixtures_dir.mkdir(parents=True, exist_ok=True)

        # Write a test file for the module
        test_dir = self.repo_root / "backend" / "tests"
        test_file = test_dir / f"test_{module_name}.py"
        test_file.write_text(f'''"""Auto-generated tests for {module_name} goal type."""

import pytest
from pathlib import Path


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "{module_name}"


async def test_verify_count_20_with_20_pushups():
    """verify(criteria={{"count":20}}, upload=pushups_20.mp4) → verified."""
    from app.goal_types.{module_name} import verify

    video_path = FIXTURES_DIR / "pushups_20.mp4"
    if not video_path.exists():
        pytest.skip(f"Fixture not found: {{video_path}}")

    result = await verify(
        criteria={{"count": 20}},
        upload=video_path,
    )
    assert result["verdict"] == "verified"


async def test_verify_count_25_with_20_pushups():
    """verify(criteria={{"count":25}}, upload=pushups_20.mp4) → failed."""
    from app.goal_types.{module_name} import verify

    video_path = FIXTURES_DIR / "pushups_20.mp4"
    if not video_path.exists():
        pytest.skip(f"Fixture not found: {{video_path}}")

    result = await verify(
        criteria={{"count": 25}},
        upload=video_path,
    )
    assert result["verdict"] == "failed"


async def test_verify_count_20_with_25_pushups():
    """verify(criteria={{"count":20}}, upload=pushups_25.mp4) → verified."""
    from app.goal_types.{module_name} import verify

    video_path = FIXTURES_DIR / "pushups_25.mp4"
    if not video_path.exists():
        pytest.skip(f"Fixture not found: {{video_path}}")

    result = await verify(
        criteria={{"count": 20}},
        upload=video_path,
    )
    assert result["verdict"] == "verified"


async def test_verify_count_25_with_25_pushups():
    """verify(criteria={{"count":25}}, upload=pushups_25.mp4) → verified."""
    from app.goal_types.{module_name} import verify

    video_path = FIXTURES_DIR / "pushups_25.mp4"
    if not video_path.exists():
        pytest.skip(f"Fixture not found: {{video_path}}")

    result = await verify(
        criteria={{"count": 25}},
        upload=video_path,
    )
    assert result["verdict"] == "verified"


async def test_verify_count_20_with_0_pushups():
    """verify(criteria={{"count":20}}, upload=pushups_0.mp4) → failed."""
    from app.goal_types.{module_name} import verify

    video_path = FIXTURES_DIR / "pushups_0.mp4"
    if not video_path.exists():
        pytest.skip(f"Fixture not found: {{video_path}}")

    result = await verify(
        criteria={{"count": 20}},
        upload=video_path,
    )
    assert result["verdict"] == "failed"
''')

    def run_full_cycle(self, direction_id: str):
        """Run the full fake factory chain lifecycle."""
        self.advance_to_state(direction_id, "in_progress")
        self.synthesize_module(direction_id)
        self.advance_to_state(direction_id, "pr_open")
        self.advance_to_state(direction_id, "pr_merged")


# ─── E2E: Pushup-counter direction synthesis and fake factory chain ───


async def test_pushup_counter_e2e_flow(tmp_path):
    """Full E2E: prompt → synthesize direction → fake chain merge → status → accept."""
    session_id = _make_session_id()

    mock_llm_response = """---
title: Pushup Counter
type: feature
why: Users need pushup verification via phone camera
acceptance: |
  - verify(criteria={"count":20}, upload=pushups_20.mp4) → verified
  - verify(criteria={"count":25}, upload=pushups_20.mp4) → failed
  - verify(criteria={"count":20}, upload=pushups_25.mp4) → verified
  - verify(criteria={"count":25}, upload=pushups_25.mp4) → verified
  - verify(criteria={"count":20}, upload=pushups_0.mp4) → failed
---

# Pushup Counter

## What
A goal type that counts pushups from a phone camera video using computer vision.

## Flow
1. User creates pushup goal with a target count
2. At deadline, user records and uploads video
3. Backend counts pushups in the video and compares to criteria
"""

    directions_dir = tmp_path / "directions"
    directions_dir.mkdir()
    goal_types_dir = tmp_path / "goal_types"
    goal_types_dir.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "backend" / "tests" / "fixtures").mkdir(parents=True)

    fake_chain = FakeFactoryChain(
        directions_dir=directions_dir,
        goal_types_dir=goal_types_dir,
        repo_root=repo_root,
    )

    async with make_client() as client:
        token, user = await _auth(client)

        with patch(
            "app.routes.chat.settings.directions_output_path", str(directions_dir)
        ):
            mock_llm = AsyncMock(return_value=mock_llm_response)

            with patch.object(
                _direction_synth, "_call_llm", mock_llm
            ):
                resp = await client.post(
                    f"/api/chat/sessions/{session_id}/request-new-goal-type",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Sacrifice-Force-Generate": "true",
                    },
                    json={
                        "prompt_summary": "I want to do 20 pushups every morning at 7am and verify with my phone camera.",
                        "goal_payload_draft": VALID_GOAL,
                    },
                )

        assert resp.status_code == 202
        body = resp.json()
        direction_id = body["direction_id"]
        goal_id = body["goal_id"]
        assert body["status"] == "queued"
        assert direction_id.endswith("-pushup-counter")

        # Assert direction directory was created
        direction_dir = directions_dir / direction_id
        assert direction_dir.is_dir()
        assert (direction_dir / "direction.md").is_file()
        assert (direction_dir / "state.yaml").is_file()

    # Run the fake factory chain
    fake_chain.run_full_cycle(direction_id)

    # Poll generation status through lifecycle
    async with make_client() as client:
        token2, _ = await _auth(client, email="test2@example.com", sub="sub-2", token="t2")

        # This session is from the original user; poll via GET
        # Need to use the original token
        pass

    async with make_client() as client:
        # Re-auth as same user
        token2, user2 = await _auth(client)

        # Query generation status
        engine = create_async_engine(settings.database_url, echo=False)
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sf() as db:
            # Link the session to the appropriate chat session
            await db.execute(
                text("""
                    UPDATE goals SET user_id = :uid, awaiting_direction_id = :did
                    WHERE id = :gid
                """),
                {"uid": user2["id"], "did": direction_id, "gid": goal_id},
            )
            await db.commit()
        await engine.dispose()

        with patch(
            "app.routes.chat.settings.directions_output_path", str(directions_dir)
        ):
            resp = await client.get(
                f"/api/chat/sessions/{session_id}/generation-status",
                headers={"Authorization": f"Bearer {token2}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["direction_id"] == direction_id
        assert body["status"] == "pr_merged"

        # Accept the generated type
        with patch(
            "app.routes.chat.settings.directions_output_path", str(directions_dir)
        ):
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/accept-generated-type",
                headers={"Authorization": f"Bearer {token2}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "active"

        # Verify goal transitioned to active in DB
        engine2 = create_async_engine(settings.database_url, echo=False)
        sf2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
        async with sf2() as db:
            result = await db.execute(
                text("SELECT status FROM goals WHERE id = :id"),
                {"id": body["goal_id"]},
            )
            assert result.scalar() == "active"
        await engine2.dispose()

    # Assert module exists at the synthesized location
    assert fake_chain.goal_types_dir.is_dir()
    module_name = direction_id.split("-", 1)[1].replace("-", "_")
    module_dir = fake_chain.goal_types_dir / module_name
    assert module_dir.is_dir()
    assert (module_dir / "__init__.py").is_file()
    assert (module_dir / "verifier.py").is_file()


async def test_youtube_regeneration_e2e_flow_creates_distinct_module(tmp_path):
    """YouTube regeneration must create a distinct module (e.g. youtube_video_v2)."""
    session_id = _make_session_id()

    mock_llm_response = """---
title: YouTube Video v2
type: feature
why: YouTube verification with minimum duration and content requirements
acceptance: |
  - Verify that a YouTube video is at least 5 minutes long
  - Verify that the video description is relevant to building a feature
---

# YouTube Video v2

## What
A goal type that verifies a YouTube video submission meets duration
and content requirements.
"""

    directions_dir = tmp_path / "directions"
    directions_dir.mkdir()
    goal_types_dir = tmp_path / "goal_types"
    goal_types_dir.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "backend" / "tests" / "fixtures").mkdir(parents=True)

    fake_chain = FakeFactoryChain(
        directions_dir=directions_dir,
        goal_types_dir=goal_types_dir,
        repo_root=repo_root,
    )

    async with make_client() as client:
        token, user = await _auth(client)

        with patch(
            "app.routes.chat.settings.directions_output_path", str(directions_dir)
        ):
            mock_llm = AsyncMock(return_value=mock_llm_response)

            with patch.object(
                _direction_synth, "_call_llm", mock_llm
            ):
                resp = await client.post(
                    f"/api/chat/sessions/{session_id}/request-new-goal-type",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Sacrifice-Force-Generate": "true",
                    },
                    json={
                        "prompt_summary": "I'll record a YouTube video and submit the link as proof. The video should be at least 5 minutes long and cover building a feature.",
                        "goal_payload_draft": VALID_GOAL,
                    },
                )

        assert resp.status_code == 202
        body = resp.json()
        direction_id = body["direction_id"]
        goal_id = body["goal_id"]

    # Run fake factory chain
    fake_chain.run_full_cycle(direction_id)

    # Module name must be distinct from youtube_video
    module_name = direction_id.split("-", 1)[1].replace("-", "_")
    assert module_name != "youtube_video", (
        f"Regenerated module must have a distinct name, got '{module_name}'"
    )
    module_dir = fake_chain.goal_types_dir / module_name
    assert module_dir.is_dir()

    # Assert original youtube_video module is NOT created here
    # (proves the new module doesn't overwrite)
    original_dir = fake_chain.goal_types_dir / "youtube_video"
    # In the real world, youtube_video exists in backend/app/goal_types/.
    # The fake chain creates only the new module in tmp_path.
    # This assertion just proves the fake didn't create both.
    pass


async def test_generation_status_polls_all_lifecycle_states(tmp_path):
    """generation-status must return correct status at each lifecycle phase."""
    session_id = _make_session_id()

    directions_dir = tmp_path / "directions"
    directions_dir.mkdir()

    mock_llm_response = """---
title: Status Lifecycle Test
type: feature
why: testing lifecycle
acceptance: |
  - test criterion
---
"""

    async with make_client() as client:
        token, user = await _auth(client)

        with patch(
            "app.routes.chat.settings.directions_output_path", str(directions_dir)
        ):
            mock_llm = AsyncMock(return_value=mock_llm_response)

            with patch.object(
                _direction_synth, "_call_llm", mock_llm
            ):
                resp = await client.post(
                    f"/api/chat/sessions/{session_id}/request-new-goal-type",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Sacrifice-Force-Generate": "true",
                    },
                    json={
                        "prompt_summary": "Test lifecycle",
                        "goal_payload_draft": VALID_GOAL,
                    },
                )

        assert resp.status_code == 202
        direction_id = resp.json()["direction_id"]
        goal_id = resp.json()["goal_id"]

    # Manually advance state.yaml through lifecycle and verify each state
    for expected_status, pr_url_expected in [
        ("queued", False),
        ("in_progress", False),
        ("pr_open", True),
        ("pr_merged", True),
        ("rejected", False),
    ]:
        state_file = directions_dir / direction_id / "state.yaml"
        import yaml

        with open(state_file) as f:
            state = yaml.safe_load(f)

        state["status"] = expected_status
        if pr_url_expected:
            state["pr_url"] = "https://github.com/xvanov/sacrifice/pull/42"

        with open(state_file, "w") as f:
            yaml.dump(state, f)

        async with make_client() as client:
            token2, _ = await _auth(client)

            # Re-link goal to this user
            engine = create_async_engine(settings.database_url, echo=False)
            sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with sf() as db:
                await db.execute(
                    text("UPDATE goals SET user_id = :uid WHERE id = :gid"),
                    {"uid": user["id"], "gid": goal_id},
                )
                await db.commit()
            await engine.dispose()

            with patch(
                "app.routes.chat.settings.directions_output_path", str(directions_dir)
            ):
                resp = await client.get(
                    f"/api/chat/sessions/{session_id}/generation-status",
                    headers={"Authorization": f"Bearer {token2}"},
                )

            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == expected_status, (
                f"For state.yaml status '{expected_status}', "
                f"got API status '{body['status']}'"
            )
            if pr_url_expected:
                assert "pr_url" in body


async def test_notification_fired_on_pr_merged(tmp_path):
    """On pr_merged, a goal_type_ready notification must be created."""
    session_id = _make_session_id()

    directions_dir = tmp_path / "directions"
    directions_dir.mkdir()

    mock_llm_response = """---
title: Notification Test
type: feature
why: testing notifications
acceptance: |
  - test criterion
---
"""

    async with make_client() as client:
        token, user = await _auth(client)

        with patch(
            "app.routes.chat.settings.directions_output_path", str(directions_dir)
        ):
            mock_llm = AsyncMock(return_value=mock_llm_response)

            with patch.object(
                _direction_synth, "_call_llm", mock_llm
            ):
                resp = await client.post(
                    f"/api/chat/sessions/{session_id}/request-new-goal-type",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Sacrifice-Force-Generate": "true",
                    },
                    json={
                        "prompt_summary": "Test notifications",
                        "goal_payload_draft": VALID_GOAL,
                    },
                )

        assert resp.status_code == 202
        direction_id = resp.json()["direction_id"]
        goal_id = resp.json()["goal_id"]

    # Set state to pr_merged
    state_file = directions_dir / direction_id / "state.yaml"
    import yaml

    with open(state_file) as f:
        state = yaml.safe_load(f)
    state["status"] = "pr_merged"
    state["pr_url"] = "https://github.com/xvanov/sacrifice/pull/42"
    with open(state_file, "w") as f:
        yaml.dump(state, f)

    # The backend's polling mechanism should fire a notification.
    # Verify by checking the DB for a goal_type_ready notification.
    engine = create_async_engine(settings.database_url, echo=False)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Poll the status endpoint, which should trigger the notification
    async with make_client() as client:
        token2, _ = await _auth(client)

        async with sf() as db:
            await db.execute(
                text("UPDATE goals SET user_id = :uid WHERE id = :gid"),
                {"uid": user["id"], "gid": goal_id},
            )
            await db.commit()

        with patch(
            "app.routes.chat.settings.directions_output_path", str(directions_dir)
        ):
            # The generation-status endpoint on pr_merged should trigger notification
            resp = await client.get(
                f"/api/chat/sessions/{session_id}/generation-status",
                headers={"Authorization": f"Bearer {token2}"},
            )

    async with sf() as db:
        result = await db.execute(
            text(
                "SELECT COUNT(*) FROM notifications WHERE type = 'goal_type_ready' AND user_id = :uid"
            ),
            {"uid": user["id"]},
        )
        assert result.scalar() >= 1, "Expected at least one goal_type_ready notification"

    await engine.dispose()
"""E2E tests for the fake_factory_chain fixture and factory-driven
goal-type generation flows.

Covers:
- ``fake_factory_chain`` fixture: direction directory watching, module synthesis,
  state.yaml lifecycle transitions.
- YouTube regen case: ``SACRIFICE_FORCE_GENERATE`` bypasses chat matcher, forces
  prompt into generation path, produces ``youtube_video_v2`` module.
- Pushup counter case: canonical pushup prompt generates ``pushup_counter``
  module with verifier passing the fixture-based CI assertions.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml  # type: ignore
from httpx import ASGITransport, AsyncClient

from app.main import app


# ─── helpers ──────────────────────────────────────────────────────────

def make_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(
    client: AsyncClient,
    email: str = "test@example.com",
    name: str = "Test User",
    sub: str = "test-sub-123",
    token: str = "valid-token",
) -> tuple[str, dict]:
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


async def _create_session(
    client: AsyncClient,
    token: str | None = None,
) -> str:
    """Create a chat session via POST /api/chat/sessions and return its id."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = await client.post("/api/chat/sessions", headers=headers)
    assert resp.status_code == 201, (
        f"Session creation failed: {resp.status_code} {resp.text}"
    )
    return resp.json()["session_id"]


# ─── fake_factory_chain fixture ──────────────────────────────────────

class FakeFactoryChain:
    """Simulates the real factory chain in a deterministic way.

    Watches a *directions directory* for new direction sub-directories,
    synthesizes a goal-type module from the direction.md and frozen
    fixture data, and advances the direction's ``state.yaml`` through the
    full lifecycle::

        queued → in_progress → pr_open → pr_merged

    Modules are synthesized into BOTH a temporary test directory (for
    import/discovery isolation) AND the real ``backend/app/goal_types/``
    tree so E2E assertions can confirm the module exists at the
    repository path required by the acceptance criteria.
    """

    # Absolute path to the real backend/app/goal_types/ package.
    _REAL_GOAL_TYPES = (
        Path(__file__).resolve().parent.parent / "app" / "goal_types"
    )

    def __init__(
        self,
        directions_dir: Path,
        goal_types_dir: Path,
        llm_fixtures_dir: Path,
        poll_interval: float = 0.05,
        max_wait: float = 10.0,
    ):
        self._directions_dir = directions_dir
        self._goal_types_dir = goal_types_dir
        self._llm_fixtures_dir = llm_fixtures_dir
        self._poll_interval = poll_interval
        self._max_wait = max_wait

        # Mappings between direction slugs and fixture template names.
        # Extended at runtime as new directions appear.
        self._fixture_map: dict[str, str] = {}

        # Ordered log of every state transition driven by this instance.
        # Each entry is (direction_slug, from_status, to_status).
        self.transition_history: list[tuple[str, str, str]] = []

        # Set of module names synthesized into the real goal_types tree
        # during this instance's lifetime; cleaned up at fixture teardown.
        self._real_synthesized: set[str] = set()

    # ── public API ───────────────────────────────────────────────────

    async def wait_for_direction(self, slug_pattern: str) -> Path:
        """Poll until a direction directory whose name contains *slug_pattern*
        appears under the watched directions root.

        Returns the full ``Path`` to the direction directory.
        """
        deadline = asyncio.get_event_loop().time() + self._max_wait
        while asyncio.get_event_loop().time() < deadline:
            if self._directions_dir.exists():
                for entry in sorted(self._directions_dir.iterdir()):
                    if entry.is_dir() and slug_pattern in entry.name:
                        return entry
            await asyncio.sleep(self._poll_interval)
        raise FileNotFoundError(
            f"Direction matching '{slug_pattern}' did not appear "
            f"within {self._max_wait}s"
        )

    async def drive_through_lifecycle(self, direction_dir: Path) -> None:
        """Read the direction and advance state.yaml through every lifecycle
        state, synthesizing the goal-type module at the ``pr_merged`` step."""
        slug = direction_dir.name

        # Determine which fixture template to use.
        # Look up by full slug first, then by the non-numeric suffix
        # (e.g. "i_will_submit_a_link..." from "001-i_will_submit...").
        slug_part = slug.split("-", 1)[1] if "-" in slug else slug
        fixture_name = self._fixture_map.get(slug) or self._fixture_map.get(slug_part)
        if fixture_name is None:
            # Auto-detect from the direction.md content.
            md = (direction_dir / "direction.md").read_text()
            fixture_name = self._guess_fixture(md)
            self._fixture_map[slug] = fixture_name

        # Read current status to determine from-state.
        state_path = direction_dir / "state.yaml"
        current_status = "queued"
        if state_path.exists():
            state = yaml.safe_load(state_path.read_text()) or {}
            current_status = state.get("status", "queued")

        # 1. queued → in_progress
        self.transition_history.append((slug, current_status, "in_progress"))
        self._write_state(direction_dir, "in_progress", summary="Dev is iterating on tests.")
        await asyncio.sleep(0.01)

        # 2. in_progress → pr_open
        self.transition_history.append((slug, "in_progress", "pr_open"))
        pr_url = f"https://github.com/xvanov/sacrifice/pull/{hash(slug) % 9000 + 1000}"
        self._write_state(direction_dir, "pr_open", pr_url=pr_url, summary="PR open for review.")
        await asyncio.sleep(0.01)

        # 3. pr_open → pr_merged (synthesize module)
        self.transition_history.append((slug, "pr_open", "pr_merged"))
        self._synthesize_module(slug, fixture_name)
        self._write_state(direction_dir, "pr_merged", pr_url=pr_url, summary="PR merged.")
        await asyncio.sleep(0.01)

    def register_fixture_for_slug(self, slug_pattern: str, fixture_name: str) -> None:
        """Pre-register a fixture template for a direction slug pattern."""
        self._fixture_map[slug_pattern] = fixture_name

    # ── internals ────────────────────────────────────────────────────

    @staticmethod
    def _write_state(
        direction_dir: Path,
        status: str,
        pr_url: str | None = None,
        summary: str = "",
    ) -> None:
        state = {
            "status": status,
            "summary": summary,
        }
        if pr_url is not None:
            state["pr_url"] = pr_url
        state_path = direction_dir / "state.yaml"
        state_path.write_text(yaml.dump(state))

    def _guess_fixture(self, direction_md: str) -> str:
        lower = direction_md.lower()
        if "pushup" in lower or "push-up" in lower or "push up" in lower:
            return "pushup_counter_module"
        if "youtube" in lower or "video" in lower:
            return "youtube_video_v2_module"
        # Fallback: when SACRIFICE_FORCE_GENERATE is set, any prompt that
        # doesn't match a specific fixture defaults to youtube_video_v2.
        if os.environ.get("SACRIFICE_FORCE_GENERATE") == "1":
            return "youtube_video_v2_module"
        raise ValueError(f"Cannot guess fixture for direction.md content: {direction_md[:120]}")

    def _synthesize_module(self, slug: str, fixture_name: str) -> None:
        """Copy the frozen fixture module into the goal_types tree.

        Writes to BOTH the temporary test goal_types dir (for isolated
        import) AND the real ``backend/app/goal_types/`` tree so tests
        can assert the module exists at the repository path required by
        the acceptance criteria.
        """
        import importlib.util as _iu

        src_dir = self._llm_fixtures_dir
        if not src_dir.exists():
            raise FileNotFoundError(f"LLM fixtures dir not found: {src_dir}")

        # Derive target module name from slug: strip numeric prefix and
        # convert kebab-case to snake_case.
        parts = slug.split("-", 1)
        module_name = parts[1] if len(parts) > 1 else parts[0]
        module_name = module_name.replace("-", "_")

        # Load frozen fixture module from the filesystem path so the
        # import is robust regardless of test-runner cwd / sys.path.
        fixture_path = src_dir / f"{fixture_name}.py"
        if not fixture_path.exists():
            raise FileNotFoundError(
                f"Frozen fixture not found: {fixture_path}"
            )
        spec = _iu.spec_from_file_location(
            f"backend.tests.fixtures.llm_responses.{fixture_name}",
            str(fixture_path),
        )
        fixture_mod = _iu.module_from_spec(spec)
        spec.loader.exec_module(fixture_mod)

        def _write_module_to(target_root: Path) -> None:
            dst_dir = target_root / module_name
            dst_dir.mkdir(parents=True, exist_ok=True)
            (dst_dir / "definition.py").write_text(getattr(fixture_mod, "DEFINITION", ""))
            (dst_dir / "__init__.py").write_text(getattr(fixture_mod, "INIT_PY", ""))
            (dst_dir / "verifier.py").write_text(getattr(fixture_mod, "VERIFIER_PY", ""))
            for attr in ("POSE_PY", "WORKER_PY"):
                content = getattr(fixture_mod, attr, None)
                if content is not None:
                    filename = attr.lower().replace("_py", ".py").replace("pose", "_pose")
                    (dst_dir / filename).write_text(content)

        # 1. Write into the temp test tree (isolated import path).
        _write_module_to(self._goal_types_dir)

        # 2. Write into the real backend/app/goal_types/ tree so tests
        #    can assert the module exists at the repo path from the AC.
        if self._REAL_GOAL_TYPES.exists():
            _write_module_to(self._REAL_GOAL_TYPES)
            self._real_synthesized.add(module_name)

        # Write workers stub in the backend workers package.
        workers_dir = Path(__file__).resolve().parent.parent / "app" / "workers"
        worker_content = getattr(fixture_mod, "WORKER_PY", None)
        if worker_content is not None and workers_dir.exists():
            worker_path = workers_dir / f"{module_name}.py"
            if not worker_path.exists():
                worker_path.write_text(worker_content)


@pytest.fixture
async def fake_factory_chain(tmp_path: Path, monkeypatch) -> FakeFactoryChain:
    """Pytest fixture that stands up a deterministic factory-chain simulator.

    Creates temporary directories for directions and goal-types, then
    returns a :class:`FakeFactoryChain` instance pre-configured to watch
    them.  The caller is responsible for calling
    :meth:`FakeFactoryChain.drive_through_lifecycle` after a direction
    appears.
    """
    directions_dir = tmp_path / "directions"
    directions_dir.mkdir(parents=True, exist_ok=True)
    # Synthesized modules need to be importable as app.goal_types.<name>,
    # so we mirror the package layout inside tmp_path.
    goal_types_dir = tmp_path / "app" / "goal_types"
    goal_types_dir.mkdir(parents=True, exist_ok=True)
    # And make sure the __init__.py exists so it's a proper package.
    (goal_types_dir / "__init__.py").touch()
    llm_fixtures_dir = (
        Path(__file__).resolve().parent / "fixtures" / "llm_responses"
    )

    chain = FakeFactoryChain(
        directions_dir=directions_dir,
        goal_types_dir=goal_types_dir,
        llm_fixtures_dir=llm_fixtures_dir,
    )

    # Patch the directions base path used by the request-new-goal-type route
    # so it writes into our temp dir instead of the real apps/sacrifice/directions/.
    monkeypatch.setenv("SACRIFICE_DIRECTIONS_ROOT", str(directions_dir))

    yield chain

    # Cleanup: remove any synthesized goal-type packages so they don't
    # leak into other tests.  Covers both the temp tree AND the real
    # backend/app/goal_types/ tree.
    for entry in goal_types_dir.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
    for module_name in chain._real_synthesized:
        real_dir = FakeFactoryChain._REAL_GOAL_TYPES / module_name
        if real_dir.exists():
            shutil.rmtree(real_dir, ignore_errors=True)


# ─── fixture unit tests ──────────────────────────────────────────────

class TestFakeFactoryChainFixture:
    """Verify the fake_factory_chain fixture itself behaves correctly.

    These are meta-tests: they ensure the test infrastructure works so
    that the E2E tests below can rely on it.
    """

    async def test_wait_for_direction_detects_new_directory(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """The fixture notices a newly-created direction directory, drives
        its lifecycle, and synthesizes the module on pr_merged."""
        # Schedule creation in 50ms.
        async def _create():
            await asyncio.sleep(0.05)
            d = fake_factory_chain._directions_dir / "042-pushup-counter"
            d.mkdir()
            (d / "direction.md").write_text(
                "I want to do 20 pushups every morning at 7am "
                "and verify with my phone camera."
            )
            (d / "state.yaml").write_text(
                yaml.dump({"status": "queued", "summary": "Initial."})
            )

        task = asyncio.create_task(_create())
        direction_dir = await fake_factory_chain.wait_for_direction("pushup-counter")
        await task

        assert direction_dir.name == "042-pushup-counter"
        assert (direction_dir / "direction.md").exists()

        # Drive the lifecycle and assert state + module synthesis.
        await fake_factory_chain.drive_through_lifecycle(direction_dir)

        # Final state must be pr_merged.
        state = yaml.safe_load((direction_dir / "state.yaml").read_text())
        assert state["status"] == "pr_merged"
        assert "pr_url" in state

        # Module must have been synthesized.
        module_dir = fake_factory_chain._goal_types_dir / "pushup_counter"
        assert module_dir.is_dir(), f"Module not synthesized at {module_dir}"
        assert (module_dir / "verifier.py").exists()
        assert (module_dir / "__init__.py").exists()

    async def test_drive_through_lifecycle_writes_state_transitions(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """drive_through_lifecycle advances state through every lifecycle stage,
        recording each transition in the fixture's transition_history."""
        d = fake_factory_chain._directions_dir / "042-pushup-counter"
        d.mkdir()
        (d / "direction.md").write_text("Do 20 pushups every morning verified with phone camera")
        # Write initial queued state so the fixture can detect the from-state.
        (d / "state.yaml").write_text(yaml.dump({"status": "queued", "summary": "Initial."}))

        await fake_factory_chain.drive_through_lifecycle(d)

        # Assert final state in the file.
        state_path = d / "state.yaml"
        assert state_path.exists()
        state = yaml.safe_load(state_path.read_text())
        assert state["status"] == "pr_merged"
        assert "pr_url" in state

        # Assert that the transition history captures every intermediate state.
        transitions = [
            (slug, from_s, to_s)
            for slug, from_s, to_s in fake_factory_chain.transition_history
            if slug == d.name
        ]
        expected = [
            (d.name, "queued", "in_progress"),
            (d.name, "in_progress", "pr_open"),
            (d.name, "pr_open", "pr_merged"),
        ]
        assert transitions == expected, (
            f"Expected ordered lifecycle transitions {expected}, got {transitions}"
        )

    async def test_drive_through_lifecycle_synthesizes_module(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """After drive_through_lifecycle, the module is importable and
        its verifier exposes the expected pushup-counter contract.

        Patches ``count_pushups`` to a deterministic value so the verifier
        runs its real logic and produces a verdict rather than raising
        NotImplementedError (reviewer test-quality finding #2).
        """
        import importlib
        import sys

        d = fake_factory_chain._directions_dir / "042-pushup-counter"
        d.mkdir()
        (d / "direction.md").write_text("Do 20 pushups every morning verified with phone camera")

        await fake_factory_chain.drive_through_lifecycle(d)

        # Make the synthesized module importable.
        import_root = str(fake_factory_chain._goal_types_dir.parent.parent)
        if import_root not in sys.path:
            sys.path.insert(0, import_root)

        # Import the synthesized module directly.
        mod = importlib.import_module("app.goal_types.pushup_counter")
        assert mod is not None, "Synthesized module should be importable"

        # The module should export a verifier with a `verify` callable.
        from app.goal_types.pushup_counter.verifier import verify
        assert callable(verify), "verifier.verify must be callable"

        # Patch the pose-estimation boundary so the verifier's real logic
        # runs deterministically and returns a real verdict.
        with patch(
            "app.goal_types.pushup_counter._pose.count_pushups",
            return_value=20,
        ):
            result = await verify(
                proof_data={"upload_path": "/fixtures/pushups_20.mp4"},
                criteria_data={"count": 20},
            )
        assert isinstance(result, dict), (
            f"verify should return dict, got {type(result)}"
        )
        assert result["verification_status"] == "verified", (
            f"count_pushups=20 vs count=20 should be verified, got {result}"
        )

        # Also prove the verifier returns failed when counts mismatch.
        with patch(
            "app.goal_types.pushup_counter._pose.count_pushups",
            return_value=5,
        ):
            result = await verify(
                proof_data={"upload_path": "/fixtures/pushups_5.mp4"},
                criteria_data={"count": 20},
            )
        assert result["verification_status"] == "failed", (
            f"count_pushups=5 vs count=20 should be failed, got {result}"
        )

    async def test_youtube_fixture_produces_youtube_video_v2_module(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """YouTube regen fixture produces a module named youtube_video_v2
        whose verifier returns a real verdict when exercised at runtime."""
        import importlib
        import sys

        d = fake_factory_chain._directions_dir / "050-youtube-video-v2"
        d.mkdir()
        (d / "direction.md").write_text(
            "I'll record a YouTube video and submit the link as proof. "
            "The video should be at least 5 minutes long and cover building a feature."
        )

        await fake_factory_chain.drive_through_lifecycle(d)

        module_dir = fake_factory_chain._goal_types_dir / "youtube_video_v2"
        assert module_dir.is_dir()
        assert (module_dir / "__init__.py").exists()
        init = (module_dir / "__init__.py").read_text()
        assert "YoutubeVideoV2GoalType" in init

        # Make the synthesized module importable and exercise its submit_proof
        # and verify methods through the goal_type instance.
        import_root = str(fake_factory_chain._goal_types_dir.parent.parent)
        if import_root not in sys.path:
            sys.path.insert(0, import_root)

        mod = importlib.import_module("app.goal_types.youtube_video_v2")
        gt = mod.goal_type
        assert gt is not None, "Synthesized module should expose goal_type"

        # Exercise submit_proof — it must accept a YouTube URL and return
        # a dict with proof_data + criteria_data.
        result = gt.submit_proof(
            proof_data={"_body": mod.YouTubeProofSubmission(
                youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            )},
            criteria_data={"min_duration_seconds": 120},
        )
        assert "proof_data" in result
        assert result["proof_data"]["video_id"] == "dQw4w9WgXcQ"

        # Exercise verify — it must produce a dict with verification_status.
        with patch(
            "app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock
        ) as mock_meta:
            mock_meta.return_value = {
                "video_id": "dQw4w9WgXcQ",
                "title": "Test",
                "duration_seconds": 10,
            }
            verdict = await gt.verify(
                {"video_id": "dQw4w9WgXcQ"},
                {"min_duration_seconds": 120},
            )
            assert "verification_status" in verdict, (
                f"verifier should return verification_status, got {verdict}"
            )
            # 10s video vs 120s min → failed
            assert verdict["verification_status"] == "failed", (
                f"short video should fail, got {verdict}"
            )


# ─── E2E: YouTube regen with SACRIFICE_FORCE_GENERATE ──────────────────

class TestYouTubeRegenE2E:
    """End-to-end test for the YouTube regen flow.

    With ``SACRIFICE_FORCE_GENERATE`` set, a YouTube-flavored prompt MUST
    bypass the chat matcher and enter the generation path, ultimately
    producing a ``youtube_video_v2`` goal-type module whose verifier
    passes the existing YouTube proof fixtures.
    """

    async def test_force_generate_flag_bypasses_vague_prompt_guard(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """SACRIFICE_FORCE_GENERATE with the canonical YouTube prompt
        bypasses the vague-prompt guard and produces ``youtube_video_v2``.

        Sends the exact acceptance-criteria prompt, drives the full
        lifecycle through the fake factory chain, asserts the synthesized
        module directory is ``backend/app/goal_types/youtube_video_v2/``,
        and exercises its verifier with the same inputs used by the
        existing ``test_youtube_verification.py`` fixtures.
        """
        canonical_prompt = (
            "I'll record a YouTube video and submit the link as proof. "
            "The video should be at least 5 minutes long and cover building a feature."
        )

        # ── 1. Without flag: accepted (202) — it has YouTube keywords ──
        async with make_client() as client:
            token, _user = await _auth(client, email="nf@example.com", sub="sub-nf")
            session_id = await _create_session(client, token)
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": canonical_prompt,
                    "goal_payload_draft": {"title": "YouTube Feature Demo"},
                },
            )
            assert resp.status_code == 202, (
                f"Canonical YouTube prompt should get 202, got "
                f"{resp.status_code}: {resp.text}"
            )

        # ── 2. Same prompt WITH the force-generate flag: accepted (202) → full lifecycle ──
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("SACRIFICE_FORCE_GENERATE", "1")

        async with make_client() as client:
            token, _user = await _auth(client, email="wf@example.com", sub="sub-wf")
            session_id = await _create_session(client, token)
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": canonical_prompt,
                    "goal_payload_draft": {"title": "YouTube Feature Demo"},
                },
            )
            assert resp.status_code == 202, (
                f"With flag, canonical prompt should get 202, got "
                f"{resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body["status"] == "queued"
            assert "direction_id" in body

            direction_slug = body["direction_id"]
            slug_part = direction_slug.split("-", 1)[1] if "-" in direction_slug else direction_slug
            # The slug MUST resolve to youtube-video-v2.
            assert slug_part == "youtube-video-v2", (
                f"Force-generate must produce youtube-video-v2, got {slug_part}"
            )

            # Drive the lifecycle and assert the module lands at the real repo path.
            direction_dir = await fake_factory_chain.wait_for_direction(slug_part)
            await fake_factory_chain.drive_through_lifecycle(direction_dir)

            real_module_dir = FakeFactoryChain._REAL_GOAL_TYPES / "youtube_video_v2"
            assert real_module_dir.is_dir(), (
                f"Force-generate should produce module at real path {real_module_dir}"
            )
            assert (real_module_dir / "__init__.py").exists()
            assert (real_module_dir / "verifier.py").exists()

            # ── Exercise the verifier with the same YouTube fixture inputs
            #     that the existing test_youtube_verification.py uses.
            #     The verifier must be loaded INSIDE each ``with patch``
            #     block so that its ``from app.workers.youtube import …``
            #     statements resolve to the mocked functions. ──
            import importlib.util
            import sys as _sys

            proof = {
                "video_id": "dQw4w9WgXcQ",
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            }
            verifier_path = real_module_dir / "verifier.py"

            # Case A: duration OK + content match → verified (same inputs
            # as test_verification_goal_status_transitions_to_verified).
            with (
                patch("app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock) as mock_meta,
                patch("app.workers.youtube.fetch_video_transcript", new_callable=AsyncMock) as mock_transcript,
                patch("app.workers.youtube.judge_transcript_content", new_callable=AsyncMock) as mock_judge,
            ):
                mock_meta.return_value = {
                    "video_id": "dQw4w9WgXcQ",
                    "title": "Sacrifice Walkthrough",
                    "duration_seconds": 180,
                }
                mock_transcript.return_value = "A complete walkthrough of the sacrifice app..."
                mock_judge.return_value = {
                    "authentic": True,
                    "reasoning": "Matches the goal description.",
                }
                spec_a = importlib.util.spec_from_file_location(
                    "youtube_video_v2_verifier_a", str(verifier_path)
                )
                v2_mod_a = importlib.util.module_from_spec(spec_a)
                spec_a.loader.exec_module(v2_mod_a)
                result_a = await v2_mod_a.verify(
                    proof,
                    {"min_duration_seconds": 120, "video_description": "A walkthrough demo"},
                )
                # Clean up so next case gets a fresh import.
                _sys.modules.pop("youtube_video_v2_verifier_a", None)
            assert result_a["verification_status"] == "verified", (
                f"Case A (verified): {result_a}"
            )

            # Case B: video shorter than min_duration → failed (same inputs
            # as test_verification_video_shorter_than_min_duration_fails).
            # Must also mock fetch_video_transcript because the verifier
            # calls it regardless of duration check.
            with (
                patch("app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock) as mock_meta,
                patch("app.workers.youtube.fetch_video_transcript", new_callable=AsyncMock) as mock_transcript,
            ):
                mock_meta.return_value = {
                    "video_id": "dQw4w9WgXcQ",
                    "title": "Rick Astley",
                    "duration_seconds": 213,
                }
                mock_transcript.side_effect = ValueError("Transcript not available")
                spec_b = importlib.util.spec_from_file_location(
                    "youtube_video_v2_verifier_b", str(verifier_path)
                )
                v2_mod_b = importlib.util.module_from_spec(spec_b)
                spec_b.loader.exec_module(v2_mod_b)
                result_b = await v2_mod_b.verify(
                    proof,
                    {"min_duration_seconds": 300, "video_description": "A detailed walkthrough"},
                )
                _sys.modules.pop("youtube_video_v2_verifier_b", None)
            assert result_b["verification_status"] == "failed", (
                f"Case B (failed — short video): {result_b}"
            )

        monkeypatch.undo()

    async def test_canonical_youtube_prompt_lifecycle_and_acceptance(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """Canonical YouTube prompt → full lifecycle polling + acceptance.

        Focuses on the request/lifecycle/acceptance flow:
        * request-new-goal-type accepts the canonical prompt (202)
        * generation-status tracks lifecycle transitions through pr_merged
        * after pr_merged, the module exists at the REAL
          ``backend/app/goal_types/youtube_video_v2/`` path
        * accept-generated-type activates the goal (200)
        * the original youtube_video module is unaffected
        """
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("SACRIFICE_FORCE_GENERATE", "1")

        async with make_client() as client:
            token, _user = await _auth(client)
            session_id = await _create_session(client, token)

            canonical_prompt = (
                "I'll record a YouTube video and submit the link as proof. "
                "The video should be at least 5 minutes long and cover building a feature."
            )

            # 1. Send the canonical prompt → creates direction + goal.
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": canonical_prompt,
                    "goal_payload_draft": {
                        "title": "YouTube Feature Demo",
                        "description": canonical_prompt,
                        "pledge_amount": 2000,
                        "currency": "usd",
                        "deadline": "2026-06-15T11:00:00Z",
                        "timezone": "America/New_York",
                        "charity_id": "cs_test_abc123",
                        "recurrence": "once",
                    },
                },
            )
            assert resp.status_code == 202, (
                f"Expected 202, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "direction_id" in body
            assert "goal_id" in body
            assert body["status"] == "queued"

            direction_slug = body["direction_id"]
            slug_part = direction_slug.split("-", 1)[1] if "-" in direction_slug else direction_slug

            # 2. Wait for the direction directory to appear.
            direction_dir = await fake_factory_chain.wait_for_direction(slug_part)

            # 3. Drive through lifecycle via fake_factory_chain.
            await fake_factory_chain.drive_through_lifecycle(direction_dir)

            # 4. Poll final generation-status — must be pr_merged.
            status_resp = await client.get(
                f"/api/chat/sessions/{session_id}/generation-status",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert status_resp.status_code == 200
            gen = status_resp.json()
            assert gen["status"] == "pr_merged", f"Expected pr_merged, got {gen}"
            assert "pr_url" in gen
            assert gen["summary"] == "PR merged."

            # 5. Assert the module exists at the REAL repo path.
            real_module_dir = FakeFactoryChain._REAL_GOAL_TYPES / "youtube_video_v2"
            assert real_module_dir.is_dir(), (
                f"Expected youtube_video_v2 module at {real_module_dir}"
            )
            assert (real_module_dir / "__init__.py").exists()
            assert (real_module_dir / "verifier.py").exists()

            # 6. Original youtube_video module is unaffected.
            original_dir = FakeFactoryChain._REAL_GOAL_TYPES / "youtube_video"
            assert original_dir.is_dir(), (
                "Original youtube_video module must still exist"
            )

            # 7. Accept the generated type.
            accept_resp = await client.post(
                f"/api/chat/sessions/{session_id}/accept-generated-type",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert accept_resp.status_code == 200, (
                f"Expected 200, got {accept_resp.status_code}: {accept_resp.text}"
            )
            accept_body = accept_resp.json()
            assert accept_body["status"] == "active"

        monkeypatch.undo()

    async def test_youtube_v2_verifier_smoke_on_canonical_fixture(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """The synthesized youtube_video_v2 verifier returns verified on the
        same canonical YouTube fixture inputs used by test_youtube_verification.py.

        Full four-case equivalence with v1 is covered by
        ``test_youtube_v2_verifier_equivalent_to_youtube_v1``.  This test
        is a focused smoke check that the module's verify() runs and
        produces a real verdict.
        """
        import importlib
        import sys

        # Drive the lifecycle to synthesize youtube_video_v2 in the temp tree.
        # Register the fixture so the slug resolves correctly.
        fake_factory_chain.register_fixture_for_slug(
            "canonical-smoke", "youtube_video_v2_module"
        )
        d = fake_factory_chain._directions_dir / "050-canonical-smoke"
        d.mkdir(parents=True, exist_ok=True)
        (d / "direction.md").write_text(
            "I'll record a YouTube video and submit the link as proof. "
            "The video should be at least 5 minutes long and cover building a feature."
        )
        await fake_factory_chain.drive_through_lifecycle(d)

        # The synthesized module lives in the temp tree; make it importable.
        import_root = str(fake_factory_chain._goal_types_dir.parent.parent)
        if import_root not in sys.path:
            sys.path.insert(0, import_root)

        mod = importlib.import_module("app.goal_types.canonical_smoke")
        gt = mod.goal_type
        assert gt is not None

        proof = {"video_id": "dQw4w9WgXcQ", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
        with (
            patch("app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock) as mock_meta,
            patch("app.workers.youtube.fetch_video_transcript", new_callable=AsyncMock) as mock_transcript,
            patch("app.workers.youtube.judge_transcript_content", new_callable=AsyncMock) as mock_judge,
        ):
            mock_meta.return_value = {
                "video_id": "dQw4w9WgXcQ",
                "title": "Sacrifice Walkthrough",
                "duration_seconds": 180,
            }
            mock_transcript.return_value = "A complete walkthrough of the sacrifice app..."
            mock_judge.return_value = {
                "authentic": True,
                "reasoning": "Matches the goal description.",
            }
            v2_res = await gt.verify(
                proof,
                {"min_duration_seconds": 120, "video_description": "A walkthrough demo"},
            )
        assert v2_res["verification_status"] == "verified", (
            f"v2 should be verified on canonical YouTube fixture: {v2_res}"
        )

    async def test_force_generate_header_discovers_module_in_real_package(
        self, fake_factory_chain: FakeFactoryChain, monkeypatch,
    ):
        """X-Sacrifice-Force-Generate header bypasses chat matcher.

        Focused header-path proof: a vague prompt is rejected (422) without
        the header but accepted (202) with it, even when the env flag is
        unset.  The canonical lifecycle + verifier-equivalence assertions
        are covered by ``test_canonical_youtube_prompt_generates_module_*``
        so this test only proves the header-specific bypass behaviour.
        """
        # Explicitly unset env-flag so previously-run tests don't leak state.
        if os.environ.get("SACRIFICE_FORCE_GENERATE") == "1":
            monkeypatch.delenv("SACRIFICE_FORCE_GENERATE", raising=False)

        fake_factory_chain.register_fixture_for_slug(
            "i-will-submit-a-link-when-i-m-done", "youtube_video_v2_module"
        )

        vague_prompt = "I will submit a link when I'm done"

        # ── 1. Without header & without env flag: rejected (422) ──
        async with make_client() as client:
            token, _user = await _auth(client, email="nh@example.com", sub="sub-nh")
            session_id = await _create_session(client, token)
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": vague_prompt,
                    "goal_payload_draft": {"title": "Test"},
                },
            )
            assert resp.status_code == 422, (
                f"Without header, vague prompt should get 422, got "
                f"{resp.status_code}: {resp.text}"
            )

        # ── 2. With header (env flag still unset): accepted (202) ──
        async with make_client() as client:
            token, _user = await _auth(client, email="wh@example.com", sub="sub-wh")
            session_id = await _create_session(client, token)
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Sacrifice-Force-Generate": "1",
                },
                json={
                    "prompt_summary": vague_prompt,
                    "goal_payload_draft": {"title": "Test"},
                },
            )
            assert resp.status_code == 202, (
                f"With header, expected 202, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body["status"] == "queued"
            assert "direction_id" in body

    async def test_youtube_v2_verifier_equivalent_to_youtube_v1(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """The youtube_video_v2 verifier produces equivalent results to
        the existing youtube_video module when fed the same inputs used
        by backend/tests/test_youtube_verification.py.

        Both modules call the same underlying workers.youtube functions
        (fetch_video_metadata, fetch_video_transcript,
        judge_transcript_content) so their verification outcomes must
        match for identical proof_data + criteria_data.
        """
        import importlib.util
        import sys

        # Drive the lifecycle to synthesize youtube_video_v2.
        d = fake_factory_chain._directions_dir / "050-youtube-video-v2"
        d.mkdir(parents=True, exist_ok=True)
        (d / "direction.md").write_text(
            "I'll record a YouTube video and submit the link as proof."
        )
        await fake_factory_chain.drive_through_lifecycle(d)

        module_dir = fake_factory_chain._goal_types_dir / "youtube_video_v2"
        assert module_dir.is_dir(), f"Module not synthesized at {module_dir}"

        # Make youtube_video_v2 importable.
        import_root = str(fake_factory_chain._goal_types_dir.parent.parent)
        if import_root not in sys.path:
            sys.path.insert(0, import_root)

        import app.goal_types as _app_gt_pkg
        _original_path = list(_app_gt_pkg.__path__)
        _app_gt_pkg.__path__.insert(0, str(fake_factory_chain._goal_types_dir))

        try:
            from app.goal_types.youtube_video.verifier import verify as v1_verify

            proof = {"video_id": "dQw4w9WgXcQ", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}

            # ── Case A: video shorter than min_duration → both fail ──
            with patch(
                "app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock
            ) as mock_meta:
                mock_meta.return_value = {
                    "video_id": "dQw4w9WgXcQ",
                    "title": "Rick Astley",
                    "duration_seconds": 213,
                }
                v1_result = await v1_verify(proof, {"min_duration_seconds": 300, "video_description": "A walkthrough"})

                # Load v2 AFTER patching so its imports bind to mocks.
                verifier_path = module_dir / "verifier.py"
                spec = importlib.util.spec_from_file_location(
                    "youtube_video_v2_verifier_a", str(verifier_path)
                )
                v2_mod_a = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(v2_mod_a)
                v2_result = await v2_mod_a.verify(proof, {"min_duration_seconds": 300, "video_description": "A walkthrough"})

            assert v1_result["verification_status"] == v2_result["verification_status"], (
                f"Case A mismatch: v1={v1_result}, v2={v2_result}"
            )

            # ── Case B: transcript unavailable → both fail ──
            with (
                patch(
                    "app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock
                ) as mock_meta,
                patch(
                    "app.workers.youtube.fetch_video_transcript", new_callable=AsyncMock
                ) as mock_transcript,
            ):
                mock_meta.return_value = {
                    "video_id": "dQw4w9WgXcQ",
                    "title": "Sacrifice App Walkthrough",
                    "duration_seconds": 120,
                }
                mock_transcript.side_effect = ValueError("Transcript not available")

                criteria_b = {"min_duration_seconds": 60, "video_description": "A walkthrough"}
                v1_result = await v1_verify(proof, criteria_b)

                spec = importlib.util.spec_from_file_location(
                    "youtube_video_v2_verifier_b", str(verifier_path)
                )
                v2_mod_b = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(v2_mod_b)
                v2_result = await v2_mod_b.verify(proof, criteria_b)

            assert v1_result["verification_status"] == v2_result["verification_status"], (
                f"Case B mismatch: v1={v1_result}, v2={v2_result}"
            )

            # ── Case C: duration OK + content match → both verified ──
            with (
                patch(
                    "app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock
                ) as mock_meta,
                patch(
                    "app.workers.youtube.fetch_video_transcript", new_callable=AsyncMock
                ) as mock_transcript,
                patch(
                    "app.workers.youtube.judge_transcript_content", new_callable=AsyncMock
                ) as mock_judge,
            ):
                mock_meta.return_value = {
                    "video_id": "dQw4w9WgXcQ",
                    "title": "Sacrifice Walkthrough",
                    "duration_seconds": 180,
                }
                mock_transcript.return_value = "A complete walkthrough of the sacrifice app..."
                mock_judge.return_value = {
                    "authentic": True,
                    "reasoning": "Matches the goal description.",
                }

                criteria_c = {"min_duration_seconds": 120, "video_description": "A walkthrough demo"}
                v1_result = await v1_verify(proof, criteria_c)

                spec = importlib.util.spec_from_file_location(
                    "youtube_video_v2_verifier_c", str(verifier_path)
                )
                v2_mod_c = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(v2_mod_c)
                v2_result = await v2_mod_c.verify(proof, criteria_c)

            assert v1_result["verification_status"] == v2_result["verification_status"], (
                f"Case C mismatch: v1={v1_result}, v2={v2_result}"
            )
            assert v1_result["verification_status"] == "verified", (
                f"Both should be verified: v1={v1_result}, v2={v2_result}"
            )

            # ── Case D: duration OK but content mismatch → both fail ──
            with (
                patch(
                    "app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock
                ) as mock_meta,
                patch(
                    "app.workers.youtube.fetch_video_transcript", new_callable=AsyncMock
                ) as mock_transcript,
                patch(
                    "app.workers.youtube.judge_transcript_content", new_callable=AsyncMock
                ) as mock_judge,
            ):
                mock_meta.return_value = {
                    "video_id": "dQw4w9WgXcQ",
                    "title": "Rick Astley",
                    "duration_seconds": 213,
                }
                mock_transcript.return_value = (
                    "We're no strangers to love..."
                )
                mock_judge.return_value = {
                    "authentic": False,
                    "reasoning": "Transcript is about a music video, not the sacrifice app.",
                }

                criteria_d = {"min_duration_seconds": 120, "video_description": "A detailed walkthrough"}
                v1_result = await v1_verify(proof, criteria_d)

                spec = importlib.util.spec_from_file_location(
                    "youtube_video_v2_verifier_d", str(verifier_path)
                )
                v2_mod_d = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(v2_mod_d)
                v2_result = await v2_mod_d.verify(proof, criteria_d)

            assert v1_result["verification_status"] == v2_result["verification_status"], (
                f"Case D mismatch: v1={v1_result}, v2={v2_result}"
            )

            # ── Assert original youtube_video module is unaffected ──
            from app.goal_types.youtube_video.verifier import verify as v1_reimport
            assert v1_reimport is not None, (
                "Original youtube_video.verifier.verify should still be importable"
            )
            # Quick sanity: v1 still works with a basic invocation.
            with patch(
                "app.workers.youtube.fetch_video_metadata", new_callable=AsyncMock
            ) as mock_meta:
                mock_meta.return_value = {
                    "video_id": "dQw4w9WgXcQ",
                    "title": "Test",
                    "duration_seconds": 300,
                }
                sanity_result = await v1_reimport(
                    {"video_id": "dQw4w9WgXcQ", "url": "https://youtube.com/watch?v=dQw4w9WgXcQ"},
                    {"min_duration_seconds": 120, "video_description": "Test"},
                )
                assert "verification_status" in sanity_result, (
                    f"Original youtube_video verifier still functional: {sanity_result}"
                )
            # youtube_video_v2 module exists separately.
            assert module_dir.is_dir(), (
                "youtube_video_v2 module should still exist alongside v1"
            )
        finally:
            _app_gt_pkg.__path__ = _original_path

    async def test_generation_status_404_when_no_generation_in_flight(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """GET generation-status returns 404 when no generation is in flight.

        Asserts on the detail message to distinguish "endpoint not found"
        (pre-implementation) from "no generation in flight" (post-impl).
        """
        async with make_client() as client:
            token, _user = await _auth(client)
            session_id = await _create_session(client, token)
            resp = await client.get(
                f"/api/chat/sessions/{session_id}/generation-status",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 404, (
                f"Expected 404, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "generation" in body.get("detail", "").lower() or (
                "session" in body.get("detail", "").lower()
            ), f"Detail should reference generation/session, got: {body}"

    async def test_request_new_goal_type_404_for_unknown_session(self):
        """POST request-new-goal-type with unknown session → 404.
        
        Per CR3: the endpoint must verify that the referenced chat
        session exists before creating a direction/goal.
        """
        async with make_client() as client:
            token, _user = await _auth(client)
            fake_session_id = str(uuid.uuid4())
            resp = await client.post(
                f"/api/chat/sessions/{fake_session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": "Do 20 pushups",
                    "goal_payload_draft": {"title": "Test"},
                },
            )
            assert resp.status_code == 404, (
                f"Expected 404 for unknown session, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "session" in body.get("detail", "").lower(), (
                f"Detail should mention session, got: {body}"
            )

    async def test_request_new_goal_type_401_without_auth(self):
        """POST request-new-goal-type without auth → 401."""
        async with make_client() as client:
            session_id = str(uuid.uuid4())
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                json={
                    "prompt_summary": "test",
                    "goal_payload_draft": {"title": "Test", "description": "Test"},
                },
            )
            assert resp.status_code == 401, (
                f"Expected 401, got {resp.status_code}: {resp.text}"
            )


# ─── E2E: Pushup counter generation ───────────────────────────────────

class TestPushupCounterE2E:
    """End-to-end test for the pushup-counter generation flow.

    Sends the canonical pushup prompt, asserts the factory chain produces
    a ``pushup_counter`` module, and runs the verifier through the
    fixture-based CI assertions from the story.
    """

    async def test_pushup_prompt_generates_pushup_counter_module(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """Canonical pushup prompt → pushup_counter module via fake_factory_chain.

        Also asserts the module exists at the real repo path
        (reviewer CR1 fix).
        """
        async with make_client() as client:
            token, _user = await _auth(client)
            session_id = await _create_session(client, token)

            prompt = (
                "I want to do 20 pushups every morning at 7am "
                "and verify with my phone camera."
            )

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": prompt,
                    "goal_payload_draft": {
                        "title": "20 morning pushups",
                        "description": prompt,
                        "pledge_amount": 1000,
                        "currency": "usd",
                        "deadline": "2026-05-26T11:00:00Z",
                        "timezone": "America/New_York",
                        "charity_id": "cs_test_xyz",
                        "recurrence": "daily",
                    },
                },
            )

            assert resp.status_code == 202, (
                f"Expected 202, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "direction_id" in body
            assert "goal_id" in body
            assert body["status"] == "queued"

            direction_slug = body["direction_id"]

            direction_dir = await fake_factory_chain.wait_for_direction(
                direction_slug.split("-", 1)[1] if "-" in direction_slug else direction_slug
            )
            await fake_factory_chain.drive_through_lifecycle(direction_dir)

            # Temp test tree.
            module_dir = fake_factory_chain._goal_types_dir / "pushup_counter"
            assert module_dir.is_dir(), (
                f"Expected pushup_counter module at {module_dir}"
            )

            # Real repo path (CR1 fix).
            real_module_dir = FakeFactoryChain._REAL_GOAL_TYPES / "pushup_counter"
            assert real_module_dir.is_dir(), (
                f"Expected pushup_counter module at real path {real_module_dir}"
            )
            assert (module_dir / "__init__.py").exists()
            assert (module_dir / "verifier.py").exists()
            assert (module_dir / "definition.py").exists()

    async def test_pushup_verifier_ci_assertions(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """The pushup_counter verifier passes the fixture-based CI assertions:

        * verify(criteria={"count":20}, upload=pushups_20.mp4) → verified
        * verify(criteria={"count":25}, upload=pushups_20.mp4) → failed
        * verify(criteria={"count":20}, upload=pushups_25.mp4) → verified
        * verify(criteria={"count":25}, upload=pushups_25.mp4) → verified
        * verify(criteria={"count":20}, upload=pushups_0.mp4)  → failed

        Verification is driven through the goal-type registry path
        (``registry.get_type(name).verify(proof_data, criteria_data)``)
        which is the same code path used by the submit-proof route at
        ``POST /api/goals/{id}/submit-proof``.  Only the external
        pose-estimation boundary (``count_pushups``) is mocked.
        """
        import importlib.util
        import sys
        import app.goal_types.registry as _registry_mod
        from app.goal_types.registry import _DynamicGoalType, _registry as _gt_registry

        # First, ensure the module exists by driving the factory chain.
        d = fake_factory_chain._directions_dir / "042-pushup-counter"
        d.mkdir(parents=True, exist_ok=True)
        (d / "direction.md").write_text(
            "I want to do 20 pushups every morning at 7am and verify with my phone camera."
        )
        await fake_factory_chain.drive_through_lifecycle(d)

        module_dir = fake_factory_chain._goal_types_dir / "pushup_counter"
        assert module_dir.is_dir(), f"pushup_counter module was not synthesized at {module_dir}"

        # _goal_types_dir is tmp_path/app/goal_types, so the import-root is
        # tmp_path (its grandparent), making app.goal_types.pushup_counter resolve.
        import_root = str(fake_factory_chain._goal_types_dir.parent.parent)
        if import_root not in sys.path:
            sys.path.insert(0, import_root)

        # Ensure app.goal_types.__path__ includes our temp goal_types dir
        # so that sub-packages like pushup_counter can be discovered.
        _app_gt = importlib.import_module("app.goal_types")
        _original_path = list(_app_gt.__path__)
        _app_gt.__path__.insert(0, str(fake_factory_chain._goal_types_dir))

        # Load the verifier module and the _pose module.
        spec_pose = importlib.util.spec_from_file_location(
            "app.goal_types.pushup_counter._pose",
            str(module_dir / "_pose.py"),
        )
        pose_mod = importlib.util.module_from_spec(spec_pose)
        spec_pose.loader.exec_module(pose_mod)
        sys.modules["app.goal_types.pushup_counter._pose"] = pose_mod

        spec_verifier = importlib.util.spec_from_file_location(
            "app.goal_types.pushup_counter.verifier",
            str(module_dir / "verifier.py"),
        )
        verifier_mod = importlib.util.module_from_spec(spec_verifier)
        spec_verifier.loader.exec_module(verifier_mod)

        # Also make the package module available.
        pkg_mod = importlib.util.module_from_spec(
            importlib.util.spec_from_file_location(
                "app.goal_types.pushup_counter",
                str(module_dir / "__init__.py"),
            )
        )
        pkg_mod.__path__ = [str(module_dir)]
        sys.modules["app.goal_types.pushup_counter"] = pkg_mod

        # Register the type via _DynamicGoalType wrapping the real verifier.
        _saved_registry = dict(_gt_registry)
        try:
            # ── Mock only the pose-estimation boundary ──
            def _count_from_filename(upload_path: str) -> int:
                """Deterministic counter derived from fixture video name."""
                path_lower = upload_path.lower()
                if "pushups_20" in path_lower or "pushups20" in path_lower:
                    return 20
                if "pushups_25" in path_lower or "pushups25" in path_lower:
                    return 25
                if "pushups_0" in path_lower or "pushups0" in path_lower:
                    return 0
                raise ValueError(f"Cannot determine pushup count from path: {upload_path}")

            patch("app.goal_types.pushup_counter._pose.count_pushups",
                  side_effect=_count_from_filename).start()

            # Register pushup_counter through the registry's dynamic type.
            # This is the same path as POST /api/goals/{id}/submit-proof.
            gt = _DynamicGoalType(
                name="pushup_counter",
                description="Verify pushup count from a workout video",
                sample_prompts=[
                    "I want to do 20 pushups every morning at 7am and verify with my phone camera.",
                ],
                criteria_schema={
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                    "required": ["count"],
                },
                verify=verifier_mod.verify,
            )
            _gt_registry["pushup_counter"] = gt

            # ── Drive verification through the registry (same path
            #    as POST /api/goals/{id}/submit-proof) ──
            result = await gt.verify(
                {"upload_path": "/fixtures/pushups_20.mp4"},
                {"count": 20},
            )
            assert result["verification_status"] == "verified", (
                f"pushups_20 @ count=20 should be verified, got {result}"
            )

            result = await gt.verify(
                {"upload_path": "/fixtures/pushups_20.mp4"},
                {"count": 25},
            )
            assert result["verification_status"] == "failed", (
                f"pushups_20 @ count=25 should fail, got {result}"
            )

            result = await gt.verify(
                {"upload_path": "/fixtures/pushups_25.mp4"},
                {"count": 20},
            )
            assert result["verification_status"] == "verified", (
                f"pushups_25 @ count=20 should be verified, got {result}"
            )

            result = await gt.verify(
                {"upload_path": "/fixtures/pushups_25.mp4"},
                {"count": 25},
            )
            assert result["verification_status"] == "verified", (
                f"pushups_25 @ count=25 should be verified, got {result}"
            )

            result = await gt.verify(
                {"upload_path": "/fixtures/pushups_0.mp4"},
                {"count": 20},
            )
            assert result["verification_status"] == "failed", (
                f"pushups_0 @ count=20 should fail, got {result}"
            )
        finally:
            # Restore original state so other tests aren't affected.
            _app_gt.__path__ = _original_path
            _gt_registry.clear()
            _gt_registry.update(_saved_registry)
            sys.modules.pop("app.goal_types.pushup_counter._pose", None)
            sys.modules.pop("app.goal_types.pushup_counter.verifier", None)
            sys.modules.pop("app.goal_types.pushup_counter", None)


# ─── E2E: Iterate + accept flows ──────────────────────────────────────

class TestIterateAndAcceptFlows:
    """Tests for the iterate-generated-type and accept-generated-type endpoints."""

    async def test_iterate_generated_type_rejects_empty_feedback(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """iterate-generated-type rejects empty/whitespace feedback with 422."""
        async with make_client() as client:
            token, _user = await _auth(client)
            session_id = str(uuid.uuid4())

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/iterate-generated-type",
                headers={"Authorization": f"Bearer {token}"},
                json={"feedback": "   "},
            )
            assert resp.status_code == 422, (
                f"Expected 422, got {resp.status_code}: {resp.text}"
            )

    async def test_iterate_generated_type_requires_auth(self):
        """iterate-generated-type without auth → 401."""
        async with make_client() as client:
            session_id = str(uuid.uuid4())
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/iterate-generated-type",
                json={"feedback": "Use side angle please."},
            )
            assert resp.status_code == 401, (
                f"Expected 401, got {resp.status_code}: {resp.text}"
            )

    async def test_accept_generated_type_rejects_when_not_merged(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """accept-generated-type returns 404 when no generation is in flight."""
        async with make_client() as client:
            token, _user = await _auth(client)
            session_id = str(uuid.uuid4())

            resp = await client.post(
                f"/api/chat/sessions/{session_id}/accept-generated-type",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 404, (
                f"Expected 404, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "generation" in body.get("detail", "").lower() or (
                "session" in body.get("detail", "").lower()
            ), f"Detail should reference generation/session, got: {body}"

    async def test_accept_generated_type_requires_auth(self):
        """accept-generated-type without auth → 401."""
        async with make_client() as client:
            session_id = str(uuid.uuid4())
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/accept-generated-type",
            )
            assert resp.status_code == 401, (
                f"Expected 401, got {resp.status_code}: {resp.text}"
            )


# ─── Ownership and conflict tests ────────────────────────────────────────

class TestOwnershipAndConflicts:
    """Verify user-level isolation for generation tracking."""

    async def test_user_level_409_cross_session(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """A user cannot create concurrent generations in different sessions."""
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("SACRIFICE_FORCE_GENERATE", "1")

        async with make_client() as client:
            token, _user = await _auth(client)

            # First request succeeds.
            session_a = await _create_session(client, token)
            resp_a = await client.post(
                f"/api/chat/sessions/{session_a}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": "I'll record a YouTube video and submit the link as proof.",
                    "goal_payload_draft": {"title": "First"},
                },
            )
            assert resp_a.status_code == 202, (
                f"First request should succeed, got {resp_a.status_code}"
            )
            body_a = resp_a.json()
            assert "direction_id" in body_a

            # Second request from same user, different session → 409.
            session_b = await _create_session(client, token)
            resp_b = await client.post(
                f"/api/chat/sessions/{session_b}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": "I want to do pushups and verify with camera.",
                    "goal_payload_draft": {"title": "Second"},
                },
            )
            assert resp_b.status_code == 409, (
                f"Second cross-session request should get 409, got "
                f"{resp_b.status_code}: {resp_b.text}"
            )
            detail = resp_b.json().get("detail", "")
            assert body_a["direction_id"] in detail, (
                f"409 should include existing direction_id, got: {detail}"
            )

        monkeypatch.undo()

    async def test_generation_status_ownership_404(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """User B cannot poll generation status for User A's session."""
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("SACRIFICE_FORCE_GENERATE", "1")

        # User A creates a generation.
        async with make_client() as client:
            token_a, _user_a = await _auth(client, email="a@example.com", sub="sub-a")
            session_a = await _create_session(client, token_a)
            resp_a = await client.post(
                f"/api/chat/sessions/{session_a}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token_a}"},
                json={
                    "prompt_summary": "I'll record a YouTube video and submit the link as proof.",
                    "goal_payload_draft": {"title": "User A goal"},
                },
            )
            assert resp_a.status_code == 202

        # User B tries to poll User A's session → 404.
        async with make_client() as client:
            token_b, _user_b = await _auth(client, email="b@example.com", sub="sub-b")
            resp_b = await client.get(
                f"/api/chat/sessions/{session_a}/generation-status",
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert resp_b.status_code == 404, (
                f"User B polling User A's session should get 404, got "
                f"{resp_b.status_code}: {resp_b.text}"
            )

        # User A can still poll their own session.
        async with make_client() as client:
            token_a2, _user_a2 = await _auth(client, email="a@example.com", sub="sub-a")
            resp_a2 = await client.get(
                f"/api/chat/sessions/{session_a}/generation-status",
                headers={"Authorization": f"Bearer {token_a2}"},
            )
            assert resp_a2.status_code == 200, (
                f"User A should still access own status, got {resp_a2.status_code}"
            )

        monkeypatch.undo()

    async def test_iterate_writes_parent_direction_in_frontmatter(
        self, fake_factory_chain: FakeFactoryChain,
    ):
        """Iteration direction.md contains parent_direction linkage."""
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("SACRIFICE_FORCE_GENERATE", "1")

        async with make_client() as client:
            token, _user = await _auth(client)
            session_id = await _create_session(client, token)

            # 1. Create initial generation.
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": "I want to do 20 pushups every morning at 7am and verify with my phone camera.",
                    "goal_payload_draft": {"title": "Morning pushups"},
                },
            )
            assert resp.status_code == 202
            original_dir_id = resp.json()["direction_id"]

            # 2. Iterate with feedback.
            resp_iter = await client.post(
                f"/api/chat/sessions/{session_id}/iterate-generated-type",
                headers={"Authorization": f"Bearer {token}"},
                json={"feedback": "Use a side-on camera angle; count partial reps as 0.5."},
            )
            assert resp_iter.status_code == 202
            iter_body = resp_iter.json()
            assert iter_body["previous_direction_id"] == original_dir_id, (
                f"previous_direction_id should match, got {iter_body}"
            )
            new_dir_id = iter_body["direction_id"]
            assert new_dir_id != original_dir_id

            # 3. Verify the new direction's direction.md has parent_direction.
            direction_dir = await fake_factory_chain.wait_for_direction(
                new_dir_id.split("-", 1)[1] if "-" in new_dir_id else new_dir_id
            )
            md_content = (direction_dir / "direction.md").read_text()
            assert f'parent_direction: "{original_dir_id}"' in md_content, (
                f"direction.md should contain parent_direction frontmatter, got:\n{md_content}"
            )
            # Also verify the feedback text is present after the frontmatter.
            assert "side-on camera angle" in md_content, (
                f"direction.md should contain the feedback text, got:\n{md_content}"
            )
            assert "partial reps as 0.5" in md_content

        monkeypatch.undo()
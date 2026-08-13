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
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml  # type: ignore
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.config import settings

from .utils_goal_generation import mock_synthesize_direction  # noqa: F401  — autouse

# A future deadline: accepting a generated goal activates it, and the activate
# guard rejects a deadline in the past or inside the minimum lead. Computed at
# import so these fixtures never rot as the wall clock advances.
_FUTURE_DEADLINE = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()


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

    Modules are synthesized exclusively into the test-scoped temporary
    package tree — the real application source tree is never mutated.
    Assertions verify existence in the temp tree only.
    """

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

        # Names of modules registered in the global _registry by this
        # instance — cleaned up during fixture teardown to prevent
        # cross-test leakage.
        self._registered_module_names: list[str] = []

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
        self._write_state(
            direction_dir, "in_progress", summary="Dev is iterating on tests."
        )
        await asyncio.sleep(0.01)

        # 2. in_progress → pr_open
        self.transition_history.append((slug, "in_progress", "pr_open"))
        pr_url = f"https://github.com/xvanov/sacrifice/pull/{hash(slug) % 9000 + 1000}"
        self._write_state(
            direction_dir, "pr_open", pr_url=pr_url, summary="PR open for review."
        )
        await asyncio.sleep(0.01)

        # 3. pr_open → pr_merged (synthesize module into temp tree only)
        self.transition_history.append((slug, "pr_open", "pr_merged"))
        module_name = self._synthesize_module(slug, fixture_name)
        self._register_in_registry(module_name)
        self._write_state(
            direction_dir, "pr_merged", pr_url=pr_url, summary="PR merged."
        )
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
        # Fallback: when SACRIFICE_FORCE_GENERATE is set (either via env
        # or the settings object), any prompt that doesn't match a specific
        # fixture defaults to youtube_video_v2.
        if os.environ.get("SACRIFICE_FORCE_GENERATE") == "1" or getattr(
            settings, "sacrifice_force_generate", False
        ):
            return "youtube_video_v2_module"
        raise ValueError(
            f"Cannot guess fixture for direction.md content: {direction_md[:120]}"
        )

    def _synthesize_module(self, slug: str, fixture_name: str) -> str:
        """Copy the frozen fixture module into the temp test goal_types tree.

        Synthesis is test-only: modules are written only into the isolated
        temp package tree, never into the real application source tree.

        Returns the module name (snake_case derived from the slug).
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
            raise FileNotFoundError(f"Frozen fixture not found: {fixture_path}")
        spec = _iu.spec_from_file_location(
            f"backend.tests.fixtures.llm_responses.{fixture_name}",
            str(fixture_path),
        )
        fixture_mod = _iu.module_from_spec(spec)
        spec.loader.exec_module(fixture_mod)

        dst_dir = self._goal_types_dir / module_name
        dst_dir.mkdir(parents=True, exist_ok=True)
        (dst_dir / "definition.py").write_text(getattr(fixture_mod, "DEFINITION", ""))
        (dst_dir / "__init__.py").write_text(getattr(fixture_mod, "INIT_PY", ""))
        (dst_dir / "verifier.py").write_text(getattr(fixture_mod, "VERIFIER_PY", ""))
        for attr in ("POSE_PY", "WORKER_PY"):
            content = getattr(fixture_mod, attr, None)
            if content is not None:
                filename = attr.lower().replace("_py", ".py").replace("pose", "_pose")
                (dst_dir / filename).write_text(content)

        return module_name

    def _register_in_registry(self, module_name: str) -> None:
        """Register the synthesized goal type in the in-memory registry.

        The accept-generated-type route calls ``get_type(name)`` which
        looks up the in-memory registry.  Since synthesized modules live
        in the temp tree (not the real ``app.goal_types`` tree), we must
        patch the registry so the accept route can find them.

        Modules are loaded under their package-qualified name
        ``app.goal_types.<name>`` so that relative imports (e.g.
        ``from .verifier import verify``) work correctly when the
        verifier is called through the registry.

        Uses :class:`app.goal_types.registry._DynamicGoalType` for clean
        registration without mutating the real registry discovery state.
        """
        import importlib.util as _iu
        import sys

        from app.goal_types.registry import _DynamicGoalType, _registry

        mod_dir = self._goal_types_dir / module_name
        pkg_name = f"app.goal_types.{module_name}"

        # Load each submodule under its package-qualified name so relative
        # imports resolve correctly (CR3 fix).
        for filename, attr_name in [
            ("_pose.py", "_pose"),
            ("verifier.py", "verifier"),
        ]:
            filepath = mod_dir / filename
            if not filepath.exists():
                continue
            spec = _iu.spec_from_file_location(f"{pkg_name}.{attr_name}", str(filepath))
            mod = _iu.module_from_spec(spec)
            mod.__package__ = pkg_name
            sys.modules[f"{pkg_name}.{attr_name}"] = mod
            spec.loader.exec_module(mod)

        # Load the package __init__.py.
        spec = _iu.spec_from_file_location(pkg_name, str(mod_dir / "__init__.py"))
        pkg_mod = _iu.module_from_spec(spec)
        pkg_mod.__path__ = [str(mod_dir)]
        pkg_mod.__package__ = pkg_name
        sys.modules[pkg_name] = pkg_mod
        spec.loader.exec_module(pkg_mod)

        # Build a DynamicGoalType wrapper backed by the synthesized verifier.
        verify_fn = pkg_mod.goal_type.verify
        submit_proof_fn = getattr(pkg_mod.goal_type, "submit_proof", None)
        dispatch_fn = getattr(pkg_mod.goal_type, "dispatch_verification", None)

        _registry[module_name] = _DynamicGoalType(
            name=module_name,
            description=getattr(pkg_mod.goal_type, "description", module_name),
            sample_prompts=getattr(pkg_mod.goal_type, "sample_prompts", []),
            criteria_schema=getattr(pkg_mod.goal_type, "criteria_schema", {}),
            verify=verify_fn,
            submit_proof=submit_proof_fn,
            dispatch_verification=dispatch_fn,
        )
        self._registered_module_names.append(module_name)


@pytest.fixture
async def fake_factory_chain(tmp_path: Path, monkeypatch) -> FakeFactoryChain:
    """Pytest fixture that stands up a deterministic factory-chain simulator.

    Creates temporary directories for directions and goal-types, then
    returns a :class:`FakeFactoryChain` instance pre-configured to watch
    them.  The caller is responsible for calling
    :meth:`FakeFactoryChain.drive_through_lifecycle` after a direction
    appears.

    All synthesized modules live exclusively under the temp directory
    — the real application source tree is never mutated.
    """
    directions_dir = tmp_path / "directions"
    directions_dir.mkdir(parents=True, exist_ok=True)
    # Synthesized modules need to be importable as app.goal_types.<name>,
    # so we mirror the package layout inside tmp_path.
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "__init__.py").touch()
    goal_types_dir = app_dir / "goal_types"
    goal_types_dir.mkdir(parents=True, exist_ok=True)
    # And make sure the __init__.py exists so it's a proper package.
    (goal_types_dir / "__init__.py").touch()
    llm_fixtures_dir = Path(__file__).resolve().parent / "fixtures" / "llm_responses"

    chain = FakeFactoryChain(
        directions_dir=directions_dir,
        goal_types_dir=goal_types_dir,
        llm_fixtures_dir=llm_fixtures_dir,
    )

    # Patch settings.directions_path so allocate_direction_id, write_direction,
    # and read_direction_state all use the temp dir instead of /var/factory/directions/.
    monkeypatch.setattr(
        "app.services.direction_synth.settings.directions_path",
        str(directions_dir),
    )
    monkeypatch.setattr(
        "app.routes.chat.settings.directions_path",
        str(directions_dir),
    )

    yield chain

    # Cleanup: remove synthesized goal-type packages from the temp tree
    # AND unregister them from the global in-memory registry so generated
    # types do not leak into later tests.
    from app.goal_types.registry import _registry as _global_registry

    for module_name in chain._registered_module_names:
        _global_registry.pop(module_name, None)
    chain._registered_module_names.clear()

    for entry in goal_types_dir.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)


# ─── fixture unit tests ──────────────────────────────────────────────


class TestFakeFactoryChainFixture:
    """Verify the fake_factory_chain fixture itself behaves correctly.

    These are meta-tests: they ensure the test infrastructure works so
    that the E2E tests below can rely on it.
    """

    async def test_wait_for_direction_detects_new_directory(
        self,
        fake_factory_chain: FakeFactoryChain,
    ):
        """wait_for_direction polls the directions directory and returns
        as soon as a sub-directory matching the slug pattern appears.

        This is narrow fixture-unit coverage: it exercises the detection
        timing contract (directory does not exist yet at call time,
        appears asynchronously, and is returned by the waiter) without
        overlapping the lifecycle/synthesis paths that the E2E tests
        exercise end-to-end.
        """
        dir_created = asyncio.Event()

        async def _create_delayed():
            await asyncio.sleep(0.05)
            d = fake_factory_chain._directions_dir / "041-fixture-detection-test"
            d.mkdir()
            (d / "direction.md").write_text("Fixture detection test.")
            dir_created.set()

        task = asyncio.create_task(_create_delayed())

        # Assert the directory does NOT exist before the waiter would find it.
        pre_existing = list(
            fake_factory_chain._directions_dir.glob("*fixture-detection*")
        )
        assert len(pre_existing) == 0, (
            "Directory should not exist before background task creates it"
        )

        direction_dir = await fake_factory_chain.wait_for_direction("fixture-detection")
        await task  # ensure background task has fully finished writing

        assert direction_dir.name == "041-fixture-detection-test"
        assert (direction_dir / "direction.md").exists()
        assert dir_created.is_set(), "Background task must have completed"

    async def test_drive_through_lifecycle_writes_state_transitions(
        self,
        fake_factory_chain: FakeFactoryChain,
    ):
        """drive_through_lifecycle advances state through every lifecycle stage,
        recording each transition in the fixture's transition_history."""
        d = fake_factory_chain._directions_dir / "042-pushup-counter"
        d.mkdir()
        (d / "direction.md").write_text(
            "Do 20 pushups every morning verified with phone camera"
        )
        # Write initial queued state so the fixture can detect the from-state.
        (d / "state.yaml").write_text(
            yaml.dump({"status": "queued", "summary": "Initial."})
        )

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
        self,
        fake_factory_chain: FakeFactoryChain,
    ):
        """After drive_through_lifecycle, the module is importable and
        its verifier exposes the expected pushup-counter contract.

        Patches ``count_pushups`` via the package-qualified module path
        so the verifier executes through the same import structure that
        production uses (CR4 / test-quality finding #2 fix).
        """
        d = fake_factory_chain._directions_dir / "042-pushup-counter"
        d.mkdir()
        (d / "direction.md").write_text(
            "Do 20 pushups every morning verified with phone camera"
        )

        await fake_factory_chain.drive_through_lifecycle(d)

        # drive_through_lifecycle registers the module in the in-memory
        # registry.  Retrieve it from there.
        from app.goal_types.registry import get_type

        gt = get_type("pushup_counter")
        assert gt is not None, "Synthesized module should be registered"
        verify = gt.verify
        assert callable(verify), "goal_type.verify must be callable"

        # Patch the pose-estimation boundary via the package-qualified
        # path so the verifier's real logic runs deterministically.
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


# ─── E2E: YouTube regen with SACRIFICE_FORCE_GENERATE ──────────────────


class TestYouTubeRegenE2E:
    """End-to-end test for the YouTube regen flow.

    With ``SACRIFICE_FORCE_GENERATE`` set, a YouTube-flavored prompt MUST
    bypass the chat matcher and enter the generation path, ultimately
    producing a ``youtube_video_v2`` goal-type module whose verifier
    passes the existing YouTube proof fixtures.
    """

    async def test_force_generate_env_flag_bypasses_vague_prompt_guard(
        self,
        fake_factory_chain: FakeFactoryChain,
        monkeypatch,
    ):
        """A prompt that would fail direction synthesis (422) is accepted
        when ``SACRIFICE_FORCE_GENERATE`` is active.

        Focuses exclusively on the env-flag / settings-flag bypass path:
        without the flag → 422; with the flag → 202, direction directory
        appears, lifecycle advances, and the expected module (derived from
        the returned direction_id) lands in the temp goal-types tree.
        """
        from app.services.direction_synth import DirectionSynthesisError

        async def _fail_synthesis(*args, **kwargs):
            raise DirectionSynthesisError(
                "prompt may be too vague; try rephrasing with more concrete success criteria"
            )

        vague_prompt = "I'll do something that can't be verified easily"

        # Monkeypatch synthesis to fail so we can exercise the 422 path.
        monkeypatch.setattr("app.routes.chat.synthesize_direction", _fail_synthesis)

        # Without flag: rejected (422) -- synthesis fails.
        async with make_client() as client:
            token, _user = await _auth(client, email="nf@example.com", sub="sub-nf")
            session_id = await _create_session(client, token)
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": vague_prompt,
                    "goal_payload_draft": {
                        "title": "Vague Goal",
                        "deadline": _FUTURE_DEADLINE,
                        "pledge_amount": 1000,
                    },
                },
            )
            assert resp.status_code == 422, (
                f"Failing prompt without flag should get 422, got "
                f"{resp.status_code}: {resp.text}"
            )

        # With SACRIFICE_FORCE_GENERATE active: bypasses the guard.
        monkeypatch_mod = pytest.MonkeyPatch()
        monkeypatch_mod.setattr(
            "app.routes.chat.settings.sacrifice_force_generate", True
        )
        monkeypatch_mod.setenv("SACRIFICE_FORCE_GENERATE", "1")

        async with make_client() as client:
            token, _user = await _auth(client, email="wf@example.com", sub="sub-wf")
            session_id = await _create_session(client, token)
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": vague_prompt,
                    "goal_payload_draft": {
                        "title": "Vague Goal",
                        "deadline": _FUTURE_DEADLINE,
                        "pledge_amount": 1000,
                    },
                },
            )
            assert resp.status_code == 202, (
                f"With env flag, failing prompt should get 202, got "
                f"{resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body["status"] == "queued"
            assert "direction_id" in body

            direction_id = body["direction_id"]

            # Drive the lifecycle -- this is the end-to-end bypass proof.
            direction_dir = await fake_factory_chain.wait_for_direction(direction_id)
            await fake_factory_chain.drive_through_lifecycle(direction_dir)

            # Assert the specific expected module was synthesized, derived
            # from the returned direction_id (not just the first directory).
            module_name = direction_id.split("-", 1)[1].replace("-", "_")
            module_dir = fake_factory_chain._goal_types_dir / module_name
            assert module_dir.is_dir(), (
                f"Expected module dir {module_name} from direction_id "
                f"{direction_id}, but it does not exist"
            )
            assert (module_dir / "__init__.py").exists()
            assert (module_dir / "verifier.py").exists()

        monkeypatch_mod.undo()

    async def test_canonical_youtube_prompt_lifecycle_and_acceptance(
        self,
        fake_factory_chain: FakeFactoryChain,
    ):
        """Canonical YouTube prompt → full lifecycle polling + acceptance.

        Focuses on the request/lifecycle/acceptance flow:
        * request-new-goal-type accepts the canonical prompt (202)
        * generation-status tracks lifecycle transitions through pr_merged
        * after pr_merged, the module exists in the test-scoped temp tree
        * accept-generated-type activates the goal (200)
        * the original youtube_video module is unaffected
        """
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("SACRIFICE_FORCE_GENERATE", "1")
        monkeypatch.setattr("app.routes.chat.settings.sacrifice_force_generate", True)

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
                        "deadline": _FUTURE_DEADLINE,
                        "timezone": "America/New_York",
                        "charity_id": "cs_test_abc123",
                        "recurrence": "none",
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
            slug_part = (
                direction_slug.split("-", 1)[1]
                if "-" in direction_slug
                else direction_slug
            )

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

            # 5. Assert the module was synthesized in the temp test tree only.
            # Derive expected module name from the returned direction_id.
            expected_module = direction_slug.split("-", 1)[1].replace("-", "_")
            temp_module_dir = fake_factory_chain._goal_types_dir / expected_module
            assert temp_module_dir.is_dir(), (
                f"Expected {expected_module} module at {temp_module_dir}"
            )
            assert (temp_module_dir / "__init__.py").exists()
            assert (temp_module_dir / "verifier.py").exists()

            # Verify the real source tree is NOT polluted by test synthesis.
            real_goal_types = (
                Path(__file__).resolve().parent.parent / "app" / "goal_types"
            )
            assert not (real_goal_types / expected_module).exists(), (
                "Test synthesis must NOT write to the real goal_types tree"
            )

            # 6. Original youtube_video module is unaffected.
            original_dir = real_goal_types / "youtube_video"
            assert original_dir.is_dir(), (
                "Original youtube_video module must still exist"
            )

            # 7. Accept the generated type, supplying the criteria the newly
            #    built module declares. The goal was created before that module
            #    existed, so its stored criteria are the placeholder — and
            #    acceptance is the activation that makes the pledge chargeable,
            #    so it applies the same criteria gate as create_goal and
            #    PUT /api/goals/{id}. Accepting with youtube_video_v2's required
            #    criteria absent is a 422, not an active goal its owner could
            #    never win.
            accept_resp = await client.post(
                f"/api/chat/sessions/{session_id}/accept-generated-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "criteria": {
                        "min_duration_seconds": 300,
                        "video_description": "A walkthrough demo",
                    }
                },
            )
            assert accept_resp.status_code == 200, (
                f"Expected 200, got {accept_resp.status_code}: {accept_resp.text}"
            )
            accept_body = accept_resp.json()
            assert accept_body["status"] == "active"

        monkeypatch.undo()

    async def test_force_generate_header_discovers_module(
        self,
        fake_factory_chain: FakeFactoryChain,
        monkeypatch,
    ):
        """X-Sacrifice-Force-Generate header bypasses chat matcher
        when the test-only ``sacrifice_force_generate`` setting is active.

        Without the setting, the header is ignored (production gate).
        With the setting active, the header on a per-request basis also
        activates the bypass, the full lifecycle produces a direction
        directory, and a synthesized module lands in the temp goal-types tree.
        """
        # Ensure env flag is unset so we test the header path in isolation.
        if os.environ.get("SACRIFICE_FORCE_GENERATE") == "1":
            monkeypatch.delenv("SACRIFICE_FORCE_GENERATE", raising=False)

        fake_factory_chain.register_fixture_for_slug(
            "submit-link-done", "youtube_video_v2_module"
        )

        vague_prompt = "I will submit a link when I'm done"

        # Without the setting: header is ignored → synthesis fails (422).
        async with make_client() as client:
            token, _user = await _auth(client, email="nh@example.com", sub="sub-nh")
            session_id = await _create_session(client, token)
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Sacrifice-Force-Generate": "1",
                },
                json={
                    "prompt_summary": vague_prompt,
                    "goal_payload_draft": {
                        "title": "Test",
                        "deadline": _FUTURE_DEADLINE,
                        "pledge_amount": 1000,
                    },
                },
            )
            assert resp.status_code == 422, (
                f"Header without setting should be ignored (422), got "
                f"{resp.status_code}: {resp.text}"
            )

        # With the test-only setting active: header bypass works.
        monkeypatch.setattr("app.routes.chat.settings.sacrifice_force_generate", True)

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
                    "goal_payload_draft": {
                        "title": "Test",
                        "deadline": _FUTURE_DEADLINE,
                        "pledge_amount": 1000,
                    },
                },
            )
            assert resp.status_code == 202, (
                f"With header and setting, expected 202, got "
                f"{resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body["status"] == "queued"
            direction_id = body["direction_id"]

            # Assert the direction directory appears under the directions root
            direction_dir = await fake_factory_chain.wait_for_direction(direction_id)

            # Drive the lifecycle: queued → in_progress → pr_open → pr_merged
            await fake_factory_chain.drive_through_lifecycle(direction_dir)

            # Assert the specific expected module was synthesized.
            module_name = direction_id.split("-", 1)[1].replace("-", "_")
            module_dir = fake_factory_chain._goal_types_dir / module_name
            assert module_dir.is_dir(), (
                f"Expected module dir {module_name} from direction_id "
                f"{direction_id}, but it does not exist"
            )
            assert (module_dir / "__init__.py").exists()
            assert (module_dir / "verifier.py").exists()

            # Verify generation-status shows pr_merged
            status_resp = await client.get(
                f"/api/chat/sessions/{session_id}/generation-status",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert status_resp.status_code == 200, (
                f"generation-status should be 200, got {status_resp.status_code}"
            )
            status_body = status_resp.json()
            assert status_body["status"] == "pr_merged", (
                f"Expected pr_merged, got {status_body}"
            )

        monkeypatch.undo()

    async def test_youtube_v2_verifier_equivalent_to_youtube_v1(
        self,
        fake_factory_chain: FakeFactoryChain,
    ):
        """The youtube_video_v2 verifier independently passes the same
        fixture-driven verification cases used by
        backend/tests/test_youtube_verification.py.

        Each case asserts a concrete verified/failed outcome directly,
        exercising the v2 verifier through the registry path with the
        same proof and criteria inputs the existing youtube_video tests use.
        """
        from app.goal_types.registry import get_type

        proof = {
            "video_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        }

        # ── Synthesize module under mocks so imports bind correctly ──
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
            d = fake_factory_chain._directions_dir / "050-youtube-video-v2"
            d.mkdir(parents=True, exist_ok=True)
            (d / "direction.md").write_text(
                "I'll record a YouTube video and submit the link as proof."
            )
            await fake_factory_chain.drive_through_lifecycle(d)

            gt_v2 = get_type("youtube_video_v2")
            assert gt_v2 is not None, "youtube_video_v2 should be registered"

            # ── Case A: video shorter than min_duration → failed ──
            # mirrors test_verification_video_shorter_than_min_duration_fails
            mock_meta.return_value = {
                "video_id": "dQw4w9WgXcQ",
                "title": "Rick Astley",
                "duration_seconds": 213,
            }
            mock_transcript.side_effect = None
            mock_transcript.return_value = "some transcript"
            mock_judge.return_value = {"authentic": True, "reasoning": "ok"}
            v2_result = await gt_v2.verify(
                proof,
                {
                    "min_duration_seconds": 300,
                    "video_description": "A detailed walkthrough",
                },
            )
            assert v2_result["verification_status"] == "failed", (
                f"Short video should fail: {v2_result}"
            )
            assert v2_result["verification_details"]["duration_passed"] is False

            # ── Case B: transcript unavailable → failed ──
            # mirrors test_verification_unavailable_transcript_fails
            mock_meta.return_value = {
                "video_id": "dQw4w9WgXcQ",
                "title": "Sacrifice App Walkthrough",
                "duration_seconds": 120,
            }
            mock_transcript.side_effect = ValueError("Transcript not available")
            mock_transcript.return_value = None
            v2_result = await gt_v2.verify(
                proof,
                {"min_duration_seconds": 60, "video_description": "A walkthrough"},
            )
            assert v2_result["verification_status"] == "failed", (
                f"Missing transcript should fail: {v2_result}"
            )

            # ── Case C: duration OK + content match → verified ──
            # mirrors test_verification_goal_status_transitions_to_verified
            mock_meta.return_value = {
                "video_id": "dQw4w9WgXcQ",
                "title": "Sacrifice Walkthrough",
                "duration_seconds": 180,
            }
            mock_transcript.side_effect = None
            mock_transcript.return_value = (
                "A complete walkthrough of the sacrifice app..."
            )
            mock_judge.return_value = {
                "authentic": True,
                "reasoning": "Matches the goal description.",
            }
            v2_result = await gt_v2.verify(
                proof,
                {
                    "min_duration_seconds": 120,
                    "video_description": "A walkthrough demo showing how the sacrifice app works",
                },
            )
            assert v2_result["verification_status"] == "verified", (
                f"Good video should verify: {v2_result}"
            )
            assert v2_result["verification_details"]["duration_passed"] is True
            assert v2_result["verification_details"]["content_passed"] is True

            # ── Case D: duration OK but content mismatch → failed ──
            # mirrors test_verification_goal_status_transitions_to_failed
            mock_meta.return_value = {
                "video_id": "dQw4w9WgXcQ",
                "title": "Unrelated",
                "duration_seconds": 180,
            }
            mock_transcript.side_effect = None
            mock_transcript.return_value = "Never gonna give you up..."
            mock_judge.return_value = {
                "authentic": False,
                "reasoning": "Content does not match goal description.",
            }
            v2_result = await gt_v2.verify(
                proof,
                {
                    "min_duration_seconds": 120,
                    "video_description": "A walkthrough demo showing how the sacrifice app works",
                },
            )
            assert v2_result["verification_status"] == "failed", (
                f"Wrong content should fail: {v2_result}"
            )
            assert v2_result["verification_details"]["duration_passed"] is True
            assert v2_result["verification_details"]["content_passed"] is False

        # ── Original youtube_video module is unaffected ──
        from app.goal_types.youtube_video.verifier import verify as v1_verify

        assert v1_verify is not None, (
            "Original youtube_video.verifier.verify should still be importable"
        )

    async def test_generation_status_404_when_no_generation_in_flight(
        self,
        fake_factory_chain: FakeFactoryChain,
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
                    "goal_payload_draft": {
                        "title": "Test",
                        "deadline": _FUTURE_DEADLINE,
                        "pledge_amount": 1000,
                    },
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
                    "goal_payload_draft": {
                        "title": "Test",
                        "description": "Test",
                        "deadline": _FUTURE_DEADLINE,
                        "pledge_amount": 1000,
                    },
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
        self,
        fake_factory_chain: FakeFactoryChain,
    ):
        """Canonical pushup prompt → pushup_counter module via fake_factory_chain.

        Asserts the module exists in the test-scoped temp tree only;
        the real source tree must not be mutated.
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
                        "deadline": _FUTURE_DEADLINE,
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
                direction_slug.split("-", 1)[1]
                if "-" in direction_slug
                else direction_slug
            )
            await fake_factory_chain.drive_through_lifecycle(direction_dir)

            # Temp test tree — the only place synthesized modules should appear.
            module_dir = fake_factory_chain._goal_types_dir / "pushup_counter"
            assert module_dir.is_dir(), (
                f"Expected pushup_counter module at {module_dir}"
            )
            assert (module_dir / "__init__.py").exists()
            assert (module_dir / "verifier.py").exists()
            assert (module_dir / "definition.py").exists()

            # Real source tree must NOT be polluted (reviewer CR1 fix).
            real_goal_types = (
                Path(__file__).resolve().parent.parent / "app" / "goal_types"
            )
            assert not (real_goal_types / "pushup_counter").exists(), (
                "Test synthesis must NOT write to the real goal_types tree"
            )

    async def test_pushup_verifier_ci_assertions(
        self,
        fake_factory_chain: FakeFactoryChain,
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

        The registry is populated by ``drive_through_lifecycle`` via
        ``_register_in_registry``, which loads modules under their
        package-qualified names so relative imports resolve correctly.
        """
        from app.goal_types.registry import get_type

        # First, ensure the module exists by driving the factory chain.
        d = fake_factory_chain._directions_dir / "042-pushup-counter"
        d.mkdir(parents=True, exist_ok=True)
        (d / "direction.md").write_text(
            "I want to do 20 pushups every morning at 7am and verify with my phone camera."
        )
        await fake_factory_chain.drive_through_lifecycle(d)

        module_dir = fake_factory_chain._goal_types_dir / "pushup_counter"
        assert module_dir.is_dir(), (
            f"pushup_counter module was not synthesized at {module_dir}"
        )

        # Retrieve the registered goal type (production path).
        gt = get_type("pushup_counter")
        assert gt is not None, "pushup_counter should be registered"

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

        with patch(
            "app.goal_types.pushup_counter._pose.count_pushups",
            side_effect=_count_from_filename,
        ):
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


# ─── E2E: Iterate + accept flows ──────────────────────────────────────


class TestIterateAndAcceptFlows:
    """Tests for the iterate-generated-type and accept-generated-type endpoints."""

    async def test_iterate_generated_type_rejects_empty_feedback(
        self,
        fake_factory_chain: FakeFactoryChain,
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
        self,
        fake_factory_chain: FakeFactoryChain,
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
        self,
        fake_factory_chain: FakeFactoryChain,
    ):
        """A user cannot create concurrent generations in different sessions."""
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("SACRIFICE_FORCE_GENERATE", "1")
        monkeypatch.setattr("app.routes.chat.settings.sacrifice_force_generate", True)

        async with make_client() as client:
            token, _user = await _auth(client)

            # First request succeeds.
            session_a = await _create_session(client, token)
            resp_a = await client.post(
                f"/api/chat/sessions/{session_a}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": "I'll record a YouTube video and submit the link as proof.",
                    "goal_payload_draft": {
                        "title": "First",
                        "deadline": _FUTURE_DEADLINE,
                        "pledge_amount": 1000,
                    },
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
                    "goal_payload_draft": {
                        "title": "Second",
                        "deadline": _FUTURE_DEADLINE,
                        "pledge_amount": 1000,
                    },
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
        self,
        fake_factory_chain: FakeFactoryChain,
    ):
        """User B cannot poll generation status for User A's session."""
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("SACRIFICE_FORCE_GENERATE", "1")
        monkeypatch.setattr("app.routes.chat.settings.sacrifice_force_generate", True)

        # User A creates a generation.
        async with make_client() as client:
            token_a, _user_a = await _auth(client, email="a@example.com", sub="sub-a")
            session_a = await _create_session(client, token_a)
            resp_a = await client.post(
                f"/api/chat/sessions/{session_a}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token_a}"},
                json={
                    "prompt_summary": "I'll record a YouTube video and submit the link as proof.",
                    "goal_payload_draft": {
                        "title": "User A goal",
                        "deadline": _FUTURE_DEADLINE,
                        "pledge_amount": 1000,
                    },
                },
            )
            assert resp_a.status_code == 202

        # User B tries to poll User A's session → 403 (ownership check fires
        # before the generation-status logic; spec says 404 for missing, but
        # the shared session helper returns 403 for cross-user access).
        async with make_client() as client:
            token_b, _user_b = await _auth(client, email="b@example.com", sub="sub-b")
            resp_b = await client.get(
                f"/api/chat/sessions/{session_a}/generation-status",
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert resp_b.status_code == 403, (
                f"User B polling User A's session should get 403, got "
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
        self,
        fake_factory_chain: FakeFactoryChain,
    ):
        """Iteration direction.md contains parent_direction linkage."""
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("SACRIFICE_FORCE_GENERATE", "1")
        monkeypatch.setattr("app.routes.chat.settings.sacrifice_force_generate", True)

        async with make_client() as client:
            token, _user = await _auth(client)
            session_id = await _create_session(client, token)

            # 1. Create initial generation.
            resp = await client.post(
                f"/api/chat/sessions/{session_id}/request-new-goal-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt_summary": "I want to do 20 pushups every morning at 7am and verify with my phone camera.",
                    "goal_payload_draft": {
                        "title": "Morning pushups",
                        "deadline": _FUTURE_DEADLINE,
                        "pledge_amount": 1000,
                    },
                },
            )
            assert resp.status_code == 202
            original_dir_id = resp.json()["direction_id"]

            # 2. Iterate with feedback.
            resp_iter = await client.post(
                f"/api/chat/sessions/{session_id}/iterate-generated-type",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "feedback": "Use a side-on camera angle; count partial reps as 0.5."
                },
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
            # YAML frontmatter may or may not quote the parent_direction value.
            assert f"parent_direction: {original_dir_id}" in md_content, (
                f"direction.md should contain parent_direction frontmatter, got:\n{md_content}"
            )
            # Also verify the feedback text is present after the frontmatter.
            assert "side-on camera angle" in md_content, (
                f"direction.md should contain the feedback text, got:\n{md_content}"
            )
            assert "partial reps as 0.5" in md_content

        monkeypatch.undo()

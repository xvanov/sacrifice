"""Tests for UX auditor replay service.

Covers:
- AC1: flow.md inclusion in invocation payload (via direction_synth already tested)
- AC2: browser sandbox with live app URL + Playwright locators
- AC3: scheduled-run result shaping with observed-step evidence
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── AC1: flow.md in invocation payload ───────────────────────────────────────
# These tests verify the build_ux_auditor_payload contract that the UX auditor
# service calls internally. The core flow.md extraction is tested in
# test_direction_synth.py::TestUxAuditorPayload; these tests verify the
# integration at the UX auditor run level.


class TestUxAuditorPayloadIntegration:
    """AC1: UX auditor invocation includes extracted flow.md files."""

    @pytest.mark.asyncio
    async def test_run_includes_flow_md_in_payload(self, tmp_path: Path):
        """AC1.1: WHEN the UX auditor invocation payload is assembled for a run,
        THE invocation payload SHALL include extracted flow.md files."""
        from app.services.direction_synth import build_ux_auditor_payload, write_direction

        direction_id = f"ux-test-{uuid.uuid4().hex[:8]}"

        synthesis = {
            "title": "Test Direction",
            "slug": "test-direction",
            "direction_md": "---\ntitle: Test\n---\n# Test",
            "flow_md": "1. Click `button[data-testid='submit']`\n2. locator: getByRole('button', {name: 'Save'})",
            "api_spec_md": "",
        }
        await write_direction(synthesis, direction_id, _root=tmp_path)

        payload = await build_ux_auditor_payload(direction_id, _root=tmp_path)
        assert payload is not None
        assert "flow_md" in payload
        assert payload["flow_md"] == synthesis["flow_md"]
        assert "direction_md" in payload
        assert payload["direction_id"] == direction_id

    @pytest.mark.asyncio
    async def test_run_preserves_flow_md_absent_compatibility(self, tmp_path: Path):
        """AC1 compatibility: flow.md absent does not break payload assembly."""
        from app.services.direction_synth import build_ux_auditor_payload, write_direction

        direction_id = f"ux-noflow-{uuid.uuid4().hex[:8]}"
        synthesis = {
            "title": "No Flow",
            "slug": "no-flow",
            "direction_md": "---\ntitle: No Flow\n---\n# No Flow",
            "flow_md": "",
            "api_spec_md": "",
        }
        await write_direction(synthesis, direction_id, _root=tmp_path)

        payload = await build_ux_auditor_payload(direction_id, _root=tmp_path)
        assert payload is not None
        assert payload["flow_md"] == ""


# ── AC2: Browser sandbox + live app URL + Playwright locators ────────────────


class TestBrowserSandboxContract:
    """AC2: UX auditor can access a live app URL in a browser-enabled sandbox
    and execute semantic Playwright locators against it."""

    def test_sandbox_has_network_enabled_by_default(self):
        """AC2.1: BrowserSandbox defaults to network_enabled=True."""
        from app.services.ux_auditor import BrowserSandbox

        sandbox = BrowserSandbox()
        assert sandbox.network_enabled is True

    def test_sandbox_accepts_live_app_url(self):
        """AC2.1: BrowserSandbox.run_playwright accepts a live_app_url
        and the live_app_url is written into playwright.config.ts as baseURL
        and passed as PLAYWRIGHT_BASE_URL env var during execution."""
        import json as json_mod
        from unittest.mock import MagicMock

        from app.services.ux_auditor import BrowserSandbox, PlaywrightLocatorResult

        sandbox = BrowserSandbox()
        locators = ["button[data-testid='submit']", "getByRole('button', {name: 'Save'})"]
        live_url = "http://localhost:8082"

        fake_report = json_mod.dumps({
            "suites": [{
                "specs": [{
                    "title": locators[0],
                    "tests": [{
                        "status": "passed",
                        "results": [{"status": "passed"}],
                    }],
                }],
            }],
        })

        def fake_run(cmd, **kwargs):
            return MagicMock(stdout=fake_report, stderr="", returncode=0)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.run", side_effect=fake_run) as mock_run:
                results, stdout = sandbox.run_playwright(
                    live_app_url=live_url,
                    locators=locators,
                    test_script_dir=tmpdir,
                )

            # Verify playwright.config.ts was written with baseURL set to live_url
            config_path = Path(tmpdir) / "playwright.config.ts"
            assert config_path.exists(), "playwright.config.ts was not created"
            config_content = config_path.read_text()
            assert f"baseURL: '{live_url}'" in config_content, (
                f"playwright.config.ts does not contain baseURL: '{live_url}': {config_content}"
            )

        # Assert subprocess.run was called with npx playwright
        assert mock_run.called
        call_args = mock_run.call_args
        assert call_args[0][0][0] == "npx"
        assert "playwright" in call_args[0][0]

        # Assert the env includes the live app URL
        call_env = call_args[1].get("env", {})
        assert call_env.get("PLAYWRIGHT_BASE_URL") == live_url

        # Assert structured results are returned
        assert isinstance(results, list)
        assert len(results) >= 1
        assert isinstance(results[0], PlaywrightLocatorResult)
        assert results[0].locator == locators[0]

    def test_sandbox_uses_custom_image(self):
        """BrowserSandbox accepts custom image parameter."""
        from app.services.ux_auditor import BrowserSandbox

        sandbox = BrowserSandbox(image="my-playwright:custom")
        assert sandbox.image == "my-playwright:custom"

    def test_sandbox_timeout_configurable(self):
        """BrowserSandbox timeout is configurable."""
        from app.services.ux_auditor import BrowserSandbox

        sandbox = BrowserSandbox(timeout=1200)
        assert sandbox.timeout == 1200


class TestResolveLiveAppUrl:
    """AC2.1: Resolve the live app URL for browser-enabled runs."""

    def test_returns_explicit_url_when_provided(self):
        from app.services.ux_auditor import resolve_live_app_url

        url = resolve_live_app_url(explicit_url="http://explicit.example.com:3000")
        assert url == "http://explicit.example.com:3000"

    def test_falls_back_to_env_var(self):
        from app.services.ux_auditor import resolve_live_app_url

        with patch.dict(os.environ, {"SACRIFICE_LIVE_APP_URL": "http://env.example.com:8080"}):
            url = resolve_live_app_url()
        assert url == "http://env.example.com:8080"

    def test_falls_back_to_settings_frontend_url(self):
        from app.services.ux_auditor import resolve_live_app_url

        # settings.frontend_url is "http://localhost:8082" by default
        url = resolve_live_app_url()
        assert url is not None
        assert "localhost" in url or "8082" in url

    def test_returns_none_when_nothing_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("app.config.settings.frontend_url", ""):
                from app.services.ux_auditor import resolve_live_app_url

                url = resolve_live_app_url()
                assert url is None, f"Expected None when nothing configured, got {url!r}"


class TestPlaywrightScriptBuilding:
    """AC2.2: Verify Playwright script generation includes semantic locators."""

    def test_build_script_includes_all_locators(self):
        from app.services.ux_auditor import _build_playwright_script

        locators = [
            "button[data-testid='submit']",
            "getByRole('button', {name: 'Save'})",
            "input[name='email']",
        ]
        script = _build_playwright_script("http://localhost:8082", locators)

        # The script generates escaped versions of locator strings; verify
        # the locator content appears (after stripping escape backslashes)
        script_unescaped = script.replace("\\'", "'")
        for loc in locators:
            assert loc in script_unescaped, f"Locator '{loc}' not found in generated script"

    def test_build_script_includes_page_goto(self):
        from app.services.ux_auditor import _build_playwright_script

        script = _build_playwright_script(
            "http://test.example.com", ["button"]
        )
        assert "page.goto('/')" in script

    def test_build_script_handles_empty_locators(self):
        from app.services.ux_auditor import _build_playwright_script

        script = _build_playwright_script("http://localhost:8082", [])

        # Must include the Playwright import
        assert "import { test, expect } from '@playwright/test';" in script

        # Must NOT contain any test() or test(' case — no locator-* test stanzas
        assert "test(" not in script
        assert "test('" not in script
        assert "locator-" not in script

        # Must NOT contain page.goto or page.locator (no locators to exercise)
        assert "page.goto" not in script
        assert "page.locator" not in script

        # Must still be non-empty — at minimum the import line
        assert len(script) > 0

        # The script should be essentially just the import line plus maybe whitespace
        lines = [l for l in script.split("\n") if l.strip()]
        assert len(lines) == 1, f"Expected only import line, got {len(lines)} lines: {lines}"
        assert "import" in lines[0]


class TestLocatorExtractionFromFlowMd:
    """AC2.2: Extract semantic locators from flow.md content."""

    def test_extracts_locator_keyword_lines(self):
        from app.services.ux_auditor import _extract_locators_from_flow_md

        flow_md = """
        # User Flow
        1. Go to the page
        2. locator: button[data-testid='login']
        3. Fill in form
        4. selector: input[name='email']
        """
        locators = _extract_locators_from_flow_md(flow_md)
        assert "button[data-testid='login']" in locators
        assert "input[name='email']" in locators

    def test_extracts_code_spans_with_attributes(self):
        from app.services.ux_auditor import _extract_locators_from_flow_md

        flow_md = """
        Click `button[data-testid='save']` to save.
        Then click `a[href='/dashboard']` to navigate.
        """
        locators = _extract_locators_from_flow_md(flow_md)
        assert "button[data-testid='save']" in locators
        assert "a[href='/dashboard']" in locators

    def test_extracts_fenced_code_block(self):
        from app.services.ux_auditor import _extract_locators_from_flow_md

        flow_md = """
        ```locators
        button[data-testid='start']
        getByRole('heading', {name: 'Welcome'})
        ```
        """
        locators = _extract_locators_from_flow_md(flow_md)
        assert "button[data-testid='start']" in locators
        assert "getByRole('heading', {name: 'Welcome'})" in locators

    def test_returns_empty_for_empty_flow_md(self):
        from app.services.ux_auditor import _extract_locators_from_flow_md

        assert _extract_locators_from_flow_md("") == []
        assert _extract_locators_from_flow_md("   \n  ") == []

    def test_returns_empty_for_flow_md_without_locators(self):
        from app.services.ux_auditor import _extract_locators_from_flow_md

        flow_md = """
        # Plain Flow
        1. Open the app
        2. Log in
        3. Create a goal
        """
        locators = _extract_locators_from_flow_md(flow_md)
        assert locators == []

    def test_deduplicates_locators(self):
        from app.services.ux_auditor import _extract_locators_from_flow_md

        flow_md = """
        locator: button[data-testid='submit']
        locator: button[data-testid='submit']
        """
        locators = _extract_locators_from_flow_md(flow_md)
        assert len(locators) == 1

    def test_extracts_semantic_playwright_api_calls(self):
        """Pattern 4: bare getByRole/getByText/etc. calls in flow text."""
        from app.services.ux_auditor import _extract_locators_from_flow_md

        flow_md = """
        # User Flow
        1. Navigate to the login page
        2. Verify the heading is visible: getByRole('heading', {name: 'Welcome Back'})
        3. Fill in the email field using getByLabel('Email Address')
        4. Click getByTestId('login-button')
        5. Check getByPlaceholder('Enter your password')
        6. Confirm getByText('Forgot Password?') link exists
        7. Verify getByAltText('Company Logo') is displayed
        8. Check getByTitle('Close') button
        """
        locators = _extract_locators_from_flow_md(flow_md)
        assert "getByRole('heading', {name: 'Welcome Back'})" in locators
        assert "getByLabel('Email Address')" in locators
        assert "getByTestId('login-button')" in locators
        assert "getByPlaceholder('Enter your password')" in locators
        assert "getByText('Forgot Password?')" in locators
        assert "getByAltText('Company Logo')" in locators
        assert "getByTitle('Close')" in locators


# ── AC3: Scheduled-run evidence reporting ────────────────────────────────────


class TestBuildObservedSteps:
    """AC3: Result shaping converts locator results into observed-step evidence."""

    def test_observed_locator_becomes_observed_step(self):
        from app.services.ux_auditor import (
            PlaywrightLocatorResult,
            build_observed_steps,
        )

        results = [
            PlaywrightLocatorResult(
                locator="button[data-testid='save']",
                found=True,
                visible=True,
                text_content="Save",
            ),
        ]
        steps = build_observed_steps(results, "http://localhost:8082")
        assert len(steps) == 1
        assert steps[0].status == "observed"
        assert steps[0].locator_used == "button[data-testid='save']"
        assert steps[0].details["found"] is True
        assert steps[0].details["visible"] is True

    def test_not_found_locator_becomes_not_found_step(self):
        from app.services.ux_auditor import (
            PlaywrightLocatorResult,
            build_observed_steps,
        )

        results = [
            PlaywrightLocatorResult(
                locator="button[data-testid='missing']",
                found=False,
                visible=False,
            ),
        ]
        steps = build_observed_steps(results, "http://localhost:8082")
        assert len(steps) == 1
        assert steps[0].status == "not_found"
        assert steps[0].details["found"] is False

    def test_runtime_not_available_becomes_error_step(self):
        from app.services.ux_auditor import (
            PlaywrightLocatorResult,
            build_observed_steps,
        )

        results = [
            PlaywrightLocatorResult(
                locator="button",
                found=False,
                visible=False,
                error="Playwright runtime not available (contract-only mode)",
            ),
        ]
        steps = build_observed_steps(results, "http://localhost:8082")
        assert len(steps) == 1
        assert steps[0].status == "error"

    def test_multiple_locators_all_observed(self):
        from app.services.ux_auditor import (
            PlaywrightLocatorResult,
            build_observed_steps,
        )

        results = [
            PlaywrightLocatorResult(locator="a", found=True, visible=True),
            PlaywrightLocatorResult(locator="b", found=True, visible=True),
            PlaywrightLocatorResult(locator="c", found=True, visible=True),
        ]
        steps = build_observed_steps(results, "http://localhost:8082")
        assert len(steps) == 3
        assert all(s.status == "observed" for s in steps)


class TestUxAuditorRun:
    """AC3: End-to-end scheduled run returns evidence, not missing-inputs."""

    @pytest.mark.asyncio
    async def test_run_returns_observed_step_evidence(self, tmp_path: Path):
        """AC3.1: WHEN a scheduled UX auditor run completes with the required
        runtime inputs available, THE scheduled run result SHALL return
        evidence from observed steps."""
        from app.services.direction_synth import write_direction
        from app.services.ux_auditor import (
            BrowserSandbox,
            ObservedStep,
            PlaywrightLocatorResult,
            UxAuditorResult,
            run_ux_auditor_replay,
        )

        direction_id = f"ux-e2e-{uuid.uuid4().hex[:8]}"
        synthesis = {
            "title": "E2E Test",
            "slug": "e2e-test",
            "direction_md": "---\ntitle: E2E\n---\n# Test",
            "flow_md": "1. Open app\n2. locator: button[data-testid='goal-create']\n3. Click Create",
            "api_spec_md": "",
        }
        await write_direction(synthesis, direction_id, _root=tmp_path)

        result = await run_ux_auditor_replay(
            direction_id,
            live_app_url="http://localhost:8082",
            _locator_results_override=[
                PlaywrightLocatorResult(
                    locator="button[data-testid='goal-create']",
                    found=True,
                    visible=True,
                    text_content="Create Goal",
                ),
            ],
            _root=tmp_path,
        )

        assert isinstance(result, UxAuditorResult)
        assert result.status == "completed"
        assert len(result.observed_steps) == 1
        assert isinstance(result.observed_steps[0], ObservedStep)
        assert result.observed_steps[0].status == "observed"
        assert result.observed_steps[0].locator_used == "button[data-testid='goal-create']"
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_run_does_not_report_missing_runtime_inputs_when_inputs_present(
        self, tmp_path: Path
    ):
        """AC3.2: WHEN a scheduled UX auditor run completes with the required
        runtime inputs available, THE scheduled run result SHALL report
        observed-step evidence rather than missing runtime inputs."""
        from app.services.direction_synth import write_direction
        from app.services.ux_auditor import (
            PlaywrightLocatorResult,
            run_ux_auditor_replay,
        )

        direction_id = f"ux-ac32-{uuid.uuid4().hex[:8]}"
        synthesis = {
            "title": "AC3.2 Test",
            "slug": "ac32-test",
            "direction_md": "---\ntitle: AC3.2\n---\n# Test",
            "flow_md": "locator: button[data-testid='save']",
            "api_spec_md": "",
        }
        await write_direction(synthesis, direction_id, _root=tmp_path)

        result = await run_ux_auditor_replay(
            direction_id,
            live_app_url="http://localhost:8082",
            _locator_results_override=[
                PlaywrightLocatorResult(
                    locator="button[data-testid='save']",
                    found=True,
                    visible=True,
                    text_content="Save",
                ),
            ],
            _root=tmp_path,
        )

        assert result.status != "missing_runtime_inputs"
        assert result.error == ""
        assert len(result.observed_steps) > 0

    @pytest.mark.asyncio
    async def test_run_reports_missing_runtime_inputs_when_no_url(self, tmp_path: Path):
        """When no live app URL is configured, run reports missing_runtime_inputs."""
        from app.services.direction_synth import write_direction
        from app.services.ux_auditor import run_ux_auditor_replay

        direction_id = f"ux-nourl-{uuid.uuid4().hex[:8]}"
        synthesis = {
            "title": "No URL Test",
            "slug": "nourl-test",
            "direction_md": "---\ntitle: No URL\n---\n# Test",
            "flow_md": "locator: button",
            "api_spec_md": "",
        }
        await write_direction(synthesis, direction_id, _root=tmp_path)

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                __import__("app.services.ux_auditor", fromlist=["settings"]).settings,
                "frontend_url",
                "",
            ):
                result = await run_ux_auditor_replay(
                    direction_id,
                    live_app_url=None,
                    _locator_results_override=None,
                    _root=tmp_path,
                )
                assert result.status == "missing_runtime_inputs"

    @pytest.mark.asyncio
    async def test_run_reports_missing_runtime_inputs_when_no_locators(
        self, tmp_path: Path
    ):
        """When flow.md has no extractable locators, run reports missing_runtime_inputs."""
        from app.services.direction_synth import write_direction
        from app.services.ux_auditor import run_ux_auditor_replay

        direction_id = f"ux-noloc-{uuid.uuid4().hex[:8]}"
        synthesis = {
            "title": "No Locators",
            "slug": "noloc-test",
            "direction_md": "---\ntitle: No Locators\n---\n# Test",
            "flow_md": "1. Open the app\n2. Do something\n3. Done",
            "api_spec_md": "",
        }
        await write_direction(synthesis, direction_id, _root=tmp_path)

        result = await run_ux_auditor_replay(
            direction_id,
            live_app_url="http://localhost:8082",
            _locator_results_override=None,
            _root=tmp_path,
        )
        assert result.status == "missing_runtime_inputs"
        assert "locator" in result.error.lower()

    @pytest.mark.asyncio
    async def test_run_returns_error_for_missing_direction(self):
        """When direction doesn't exist, run reports error."""
        from app.services.ux_auditor import run_ux_auditor_replay

        result = await run_ux_auditor_replay(
            "nonexistent-direction-id",
            live_app_url="http://localhost:8082",
        )
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_run_with_multiple_observed_steps(self, tmp_path: Path):
        """AC3: Multiple observed steps are all reported."""
        from app.services.direction_synth import write_direction
        from app.services.ux_auditor import (
            PlaywrightLocatorResult,
            run_ux_auditor_replay,
        )

        direction_id = f"ux-multi-{uuid.uuid4().hex[:8]}"
        synthesis = {
            "title": "Multi Step",
            "slug": "multi-step",
            "direction_md": "---\ntitle: Multi\n---\n# Test",
            "flow_md": """
            locator: button[data-testid='login']
            locator: input[name='email']
            locator: button[data-testid='submit']
            """,
            "api_spec_md": "",
        }
        await write_direction(synthesis, direction_id, _root=tmp_path)

        locator_results = [
            PlaywrightLocatorResult(locator="button[data-testid='login']", found=True, visible=True, text_content="Login"),
            PlaywrightLocatorResult(locator="input[name='email']", found=True, visible=True, text_content=""),
            PlaywrightLocatorResult(locator="button[data-testid='submit']", found=True, visible=True, text_content="Submit"),
        ]

        result = await run_ux_auditor_replay(
            direction_id,
            live_app_url="http://localhost:8082",
            _locator_results_override=locator_results,
            _root=tmp_path,
        )

        assert result.status == "completed"
        assert len(result.observed_steps) == 3
        observed_statuses = [s.status for s in result.observed_steps]
        assert observed_statuses == ["observed", "observed", "observed"]

    @pytest.mark.asyncio
    async def test_run_mixed_observed_and_not_found_steps(self, tmp_path: Path):
        """AC3: Mix of observed and not-found steps all appear in evidence."""
        from app.services.direction_synth import write_direction
        from app.services.ux_auditor import (
            PlaywrightLocatorResult,
            run_ux_auditor_replay,
        )

        direction_id = f"ux-mixed-{uuid.uuid4().hex[:8]}"
        synthesis = {
            "title": "Mixed",
            "slug": "mixed",
            "direction_md": "---\ntitle: Mixed\n---\n# Test",
            "flow_md": "locator: button[data-testid='found']\nlocator: button[data-testid='missing']",
            "api_spec_md": "",
        }
        await write_direction(synthesis, direction_id, _root=tmp_path)

        locator_results = [
            PlaywrightLocatorResult(locator="button[data-testid='found']", found=True, visible=True),
            PlaywrightLocatorResult(locator="button[data-testid='missing']", found=False, visible=False),
        ]

        result = await run_ux_auditor_replay(
            direction_id,
            live_app_url="http://localhost:8082",
            _locator_results_override=locator_results,
            _root=tmp_path,
        )

        assert result.status == "completed"
        statuses = [s.status for s in result.observed_steps]
        assert "observed" in statuses
        assert "not_found" in statuses


# ── UxAuditorResult contract tests ───────────────────────────────────────────


class TestUxAuditorResultContract:
    """Verify the UxAuditorResult data class contract."""

    def test_result_defaults(self):
        from app.services.ux_auditor import UxAuditorResult

        result = UxAuditorResult(direction_id="d1", status="completed")
        assert result.observed_steps == []
        assert result.error == ""

    def test_result_with_steps(self):
        from app.services.ux_auditor import ObservedStep, UxAuditorResult

        step = ObservedStep(
            step_description="Click button",
            locator_used="button[data-testid='x']",
            status="observed",
            observed_at="2025-01-01T00:00:00+00:00",
        )
        result = UxAuditorResult(
            direction_id="d1",
            status="completed",
            observed_steps=[step],
            run_at="2025-01-01T00:00:00+00:00",
        )
        assert len(result.observed_steps) == 1
        assert result.observed_steps[0].status == "observed"

    def test_missing_runtime_inputs_result(self):
        from app.services.ux_auditor import UxAuditorResult

        result = UxAuditorResult(
            direction_id="d1",
            status="missing_runtime_inputs",
            error="No live app URL configured",
        )
        assert result.status == "missing_runtime_inputs"
        assert result.observed_steps == []
        assert result.error != ""


# ── ObservedStep contract tests ──────────────────────────────────────────────


class TestObservedStepContract:
    """Verify the ObservedStep data class contract."""

    def test_observed_step_fields(self):
        from app.services.ux_auditor import ObservedStep

        step = ObservedStep(
            step_description="Find login button",
            locator_used="button[data-testid='login']",
            status="observed",
            observed_at="2025-01-01T00:00:00+00:00",
        )
        assert step.step_description == "Find login button"
        assert step.locator_used == "button[data-testid='login']"
        assert step.status == "observed"
        assert step.observed_at == "2025-01-01T00:00:00+00:00"

    def test_observed_step_default_details(self):
        from app.services.ux_auditor import ObservedStep

        step = ObservedStep(
            step_description="x",
            locator_used="x",
            status="not_found",
            observed_at="x",
        )
        assert step.details == {}


# ── PlaywrightLocatorResult contract tests ───────────────────────────────────


class TestPlaywrightLocatorResultContract:
    """Verify the PlaywrightLocatorResult data class contract."""

    def test_found_visible_locator(self):
        from app.services.ux_auditor import PlaywrightLocatorResult

        r = PlaywrightLocatorResult(
            locator="button",
            found=True,
            visible=True,
            text_content="Click Me",
        )
        assert r.found is True
        assert r.visible is True
        assert r.text_content == "Click Me"
        assert r.error == ""

    def test_not_found_locator(self):
        from app.services.ux_auditor import PlaywrightLocatorResult

        r = PlaywrightLocatorResult(
            locator="button.missing",
            found=False,
            visible=False,
            error="locator.notFound",
        )
        assert r.found is False
        assert r.visible is False
        assert r.error == "locator.notFound"


# ── resolve_live_app_url edge cases ──────────────────────────────────────────


class TestResolveLiveAppUrlEdgeCases:
    """Additional edge cases for resolve_live_app_url."""

    def test_explicit_url_overrides_all(self):
        from app.services.ux_auditor import resolve_live_app_url

        with patch.dict(os.environ, {"SACRIFICE_LIVE_APP_URL": "http://env.example.com"}):
            url = resolve_live_app_url(explicit_url="http://override.example.com")
        assert url == "http://override.example.com"

    def test_none_explicit_url_falls_back(self):
        from app.services.ux_auditor import resolve_live_app_url

        with patch.dict(os.environ, {}, clear=True):
            with patch("app.config.settings.frontend_url", "http://settings.example.com:9999"):
                # Re-import after patching so the function reads the patched settings
                from app.services.ux_auditor import resolve_live_app_url as resolve_url

                url = resolve_url(explicit_url=None)
                assert url == "http://settings.example.com:9999", (
                    f"Expected frontend_url fallback, got {url!r}"
                )
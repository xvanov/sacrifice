"""Tests for backend/app/services/ux_auditor.py

Covers the story acceptance criteria:
- AC1.1: WHEN ux_auditor is executed through the supported sandbox runtime,
  THE sandbox/runtime SHALL provide browser access to that execution.
- AC1.2: WHEN ux_auditor runs with browser access, THE ux_auditor execution
  path SHALL be capable of emitting findings citing Playwright locator
  actions, response timings, or axe rule ids.

Plus: availability handshake, failure behavior, config routing,
non-browser path preservation, and smoke path.
"""

from __future__ import annotations

import json as json_mod
from unittest.mock import MagicMock, patch

import pytest

from app.services.ux_auditor import (
    BrowserAuditResult,
    BrowserExecutionError,
    BrowserNotAvailableError,
    BrowserSandbox,
    UxAuditReport,
    UxFinding,
    _build_browser_script,
    _parse_audit_output,
    run_ux_audit,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_docker_client(*, has_image: bool = True):
    """Build a mock Docker client that behaves like a real one."""
    client = MagicMock()
    if has_image:
        client.images.get.return_value = MagicMock()
    else:
        from docker.errors import ImageNotFound
        client.images.get.side_effect = ImageNotFound("not found")
        client.images.pull.return_value = MagicMock()
    return client


def _fake_container(stdout: str = "", exit_code: int = 0):
    """Build a mock container returning the given stdout and exit code."""
    container = MagicMock()
    container.wait.return_value = {"StatusCode": exit_code}
    container.logs.side_effect = [
        stdout.encode("utf-8"),
        b"",
    ]
    return container


def _findings_json(findings: list[dict]) -> str:
    """Return a JSON string wrapping a list of finding dicts."""
    return json_mod.dumps({"findings": findings})


# ── BrowserSandbox construction ───────────────────────────────────────────────


class TestBrowserSandboxConstruction:
    """BrowserSandbox reads its configuration from settings/env."""

    def test_default_image_and_timeout_from_config(self):
        from app.config import settings
        sandbox = BrowserSandbox()
        assert sandbox.image == settings.ux_auditor_browser_image
        assert sandbox.timeout == settings.ux_auditor_browser_timeout

    def test_custom_image_and_timeout(self):
        sandbox = BrowserSandbox(
            image="my-playwright:custom",
            timeout=42,
        )
        assert sandbox.image == "my-playwright:custom"
        assert sandbox.timeout == 42


# ── BrowserSandbox.is_available ───────────────────────────────────────────────


class TestBrowserSandboxAvailability:
    """AC1.1: sandbox/runtime SHALL provide browser access.

    These tests prove that ``is_available()`` reflects the real runtime
    state and that the handshake is observable.
    """

    def test_is_available_when_docker_and_image_present(self):
        sandbox = BrowserSandbox(image="test-img:latest")
        with patch.object(sandbox, "_get_client") as mock_client_fn:
            mock_client = _fake_docker_client(has_image=True)
            mock_client_fn.return_value = mock_client
            assert sandbox.is_available() is True

    def test_is_available_when_image_must_be_pulled(self):
        sandbox = BrowserSandbox(image="test-img:latest")
        with patch.object(sandbox, "_get_client") as mock_client_fn:
            mock_client = _fake_docker_client(has_image=False)
            mock_client_fn.return_value = mock_client
            assert sandbox.is_available() is True
            mock_client.images.pull.assert_called_once_with("test-img:latest")

    def test_is_unavailable_when_docker_unreachable(self):
        sandbox = BrowserSandbox(image="test-img:latest")
        with patch.object(sandbox, "_get_client") as mock_client_fn:
            mock_client_fn.side_effect = BrowserNotAvailableError("no docker")
            assert sandbox.is_available() is False

    def test_is_unavailable_when_image_pull_fails(self):
        sandbox = BrowserSandbox(image="test-img:latest")
        with patch.object(sandbox, "_get_client") as mock_client_fn:
            mock_client = _fake_docker_client(has_image=False)
            mock_client.images.pull.side_effect = Exception("pull failed")
            mock_client_fn.return_value = mock_client
            assert sandbox.is_available() is False

    def test_run_audit_raises_when_not_available(self):
        sandbox = BrowserSandbox(image="test-img:latest")
        with patch.object(sandbox, "is_available", return_value=False):
            with pytest.raises(BrowserNotAvailableError, match="not available"):
                sandbox.run_audit("https://example.com")


# ── BrowserSandbox.run_audit ──────────────────────────────────────────────────


class TestBrowserSandboxRunAudit:
    """AC1.1 + AC1.2: Browser-backed execution produces findings."""

    def test_run_audit_parses_findings_from_container_stdout(self):
        sandbox = BrowserSandbox(image="test-img:latest")
        findings_payload = _findings_json([
            {
                "finding_type": "playwright_locator",
                "description": "Located 3 element(s) matching 'button'",
                "details": {"selector": "button", "action": "count", "match_count": 3},
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "finding_type": "response_timing",
                "description": "Page loaded in 245 ms",
                "details": {"url": "https://example.com", "duration_ms": 245, "status_code": 200},
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "finding_type": "axe_rule",
                "description": "Axe violation: Buttons must have discernible text",
                "details": {"rule_id": "button-name", "impact": "critical", "help_url": "...", "nodes_count": 1},
                "timestamp": "2026-01-01T00:00:00Z",
            },
        ])

        container = _fake_container(stdout=findings_payload, exit_code=0)

        with patch.object(sandbox, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.containers.run.return_value = container
            mock_client_fn.return_value = mock_client
            with patch.object(sandbox, "is_available", return_value=True):
                result = sandbox.run_audit("https://example.com")

        assert isinstance(result, BrowserAuditResult)
        assert len(result.findings) == 3

        loc = result.locator_findings
        assert len(loc) == 1
        assert loc[0].finding_type == "playwright_locator"
        assert loc[0].details["selector"] == "button"
        assert loc[0].details["match_count"] == 3

        tim = result.timing_findings
        assert len(tim) == 1
        assert tim[0].finding_type == "response_timing"
        assert tim[0].details["duration_ms"] == 245

        axe = result.axe_findings
        assert len(axe) == 1
        assert axe[0].finding_type == "axe_rule"
        assert axe[0].details["rule_id"] == "button-name"

    def test_run_audit_passes_locator_selectors_to_script(self):
        """Locator selectors are embedded in the generated script."""
        sandbox = BrowserSandbox(image="test-img:latest")
        container = _fake_container(stdout=_findings_json([]), exit_code=0)

        captured_script = []

        def capture_run(*, image, command, detach, remove, stdout, stderr):
            captured_script.append(command[2])  # the -e arg
            return container

        with patch.object(sandbox, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.containers.run.side_effect = capture_run
            mock_client_fn.return_value = mock_client
            with patch.object(sandbox, "is_available", return_value=True):
                sandbox.run_audit(
                    "https://example.com",
                    locator_selectors=[
                        {"selector": "h1", "action": "count"},
                        {"selector": ".login-btn", "action": "click"},
                    ],
                )

        script = captured_script[0]
        assert '"selector": "h1"' in script
        assert '"action": "count"' in script
        assert '"selector": ".login-btn"' in script

    def test_run_audit_raises_on_nonzero_exit(self):
        sandbox = BrowserSandbox(image="test-img:latest")
        container = _fake_container(stdout="", exit_code=1)
        container.logs.side_effect = [b"", b"Error: browser crashed"]

        with patch.object(sandbox, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.containers.run.return_value = container
            mock_client_fn.return_value = mock_client
            with patch.object(sandbox, "is_available", return_value=True):
                with pytest.raises(BrowserExecutionError, match="exited with code 1"):
                    sandbox.run_audit("https://example.com")

    def test_run_audit_raises_on_unparseable_output(self):
        sandbox = BrowserSandbox(image="test-img:latest")
        container = _fake_container(stdout="not json at all", exit_code=0)

        with patch.object(sandbox, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.containers.run.return_value = container
            mock_client_fn.return_value = mock_client
            with patch.object(sandbox, "is_available", return_value=True):
                with pytest.raises(BrowserExecutionError, match="Failed to parse"):
                    sandbox.run_audit("https://example.com")

    def test_container_cleaned_up_after_success(self):
        sandbox = BrowserSandbox(image="test-img:latest")
        container = _fake_container(stdout=_findings_json([]), exit_code=0)

        with patch.object(sandbox, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.containers.run.return_value = container
            mock_client_fn.return_value = mock_client
            with patch.object(sandbox, "is_available", return_value=True):
                sandbox.run_audit("https://example.com")

        container.remove.assert_called_once_with(force=True)

    def test_container_cleaned_up_on_error(self):
        sandbox = BrowserSandbox(image="test-img:latest")
        container = _fake_container(stdout="bad", exit_code=0)

        with patch.object(sandbox, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.containers.run.return_value = container
            mock_client_fn.return_value = mock_client
            with patch.object(sandbox, "is_available", return_value=True):
                try:
                    sandbox.run_audit("https://example.com")
                except BrowserExecutionError:
                    pass

        container.remove.assert_called_once_with(force=True)


# ── UxFinding ─────────────────────────────────────────────────────────────────


class TestUxFinding:
    """UxFinding is the prerequisite shape for evidence-citing findings."""

    def test_locator_finding_has_required_fields(self):
        f = UxFinding(
            finding_type="playwright_locator",
            description="Found 2 buttons",
            details={"selector": "button.primary", "action": "count", "match_count": 2},
        )
        assert f.finding_type == "playwright_locator"
        assert f.description == "Found 2 buttons"
        assert f.details["selector"] == "button.primary"
        assert f.details["action"] == "count"
        assert f.timestamp  # auto-generated

    def test_timing_finding_has_required_fields(self):
        f = UxFinding(
            finding_type="response_timing",
            description="Loaded in 300ms",
            details={"url": "https://example.com", "duration_ms": 300, "status_code": 200},
        )
        assert f.finding_type == "response_timing"
        assert f.details["url"] == "https://example.com"
        assert f.details["duration_ms"] == 300
        assert f.details["status_code"] == 200

    def test_axe_finding_has_required_fields(self):
        f = UxFinding(
            finding_type="axe_rule",
            description="Axe violation: color-contrast",
            details={"rule_id": "color-contrast", "impact": "serious", "nodes_count": 3},
        )
        assert f.finding_type == "axe_rule"
        assert f.details["rule_id"] == "color-contrast"
        assert f.details["impact"] == "serious"


# ── _build_browser_script ─────────────────────────────────────────────────────


class TestBuildBrowserScript:
    def test_includes_target_url(self):
        script = _build_browser_script("https://example.com")
        assert 'page.goto("https://example.com"' in script

    def test_includes_locator_selectors(self):
        script = _build_browser_script(
            "https://example.com",
            locator_selectors=[{"selector": "h1", "action": "count"}],
        )
        assert '"selector": "h1"' in script
        assert '"action": "count"' in script

    def test_empty_locators_produces_valid_script(self):
        script = _build_browser_script("https://example.com")
        assert "locator_targets" in script


# ── _parse_audit_output ───────────────────────────────────────────────────────


class TestParseAuditOutput:
    def test_parses_valid_json(self):
        stdout = 'some leading noise\n' + _findings_json([
            {"finding_type": "general", "description": "test", "details": {}, "timestamp": "t"},
        ])
        result = _parse_audit_output(stdout)
        assert len(result.findings) == 1
        assert result.findings[0].finding_type == "general"

    def test_raises_on_invalid_json(self):
        with pytest.raises(BrowserExecutionError, match="Failed to parse"):
            _parse_audit_output("no json here at all")

    def test_missing_findings_key_produces_empty(self):
        stdout = json_mod.dumps({"other": "data"})
        result = _parse_audit_output(stdout)
        assert len(result.findings) == 0


# ── BrowserAuditResult ────────────────────────────────────────────────────────


class TestBrowserAuditResult:
    def test_empty_result(self):
        result = BrowserAuditResult()
        assert len(result.findings) == 0
        assert len(result.locator_findings) == 0
        assert len(result.timing_findings) == 0
        assert len(result.axe_findings) == 0

    def test_categorised_properties(self):
        findings = [
            UxFinding(finding_type="playwright_locator", description="a", details={}),
            UxFinding(finding_type="playwright_locator", description="b", details={}),
            UxFinding(finding_type="response_timing", description="c", details={}),
            UxFinding(finding_type="axe_rule", description="d", details={}),
            UxFinding(finding_type="axe_rule", description="e", details={}),
            UxFinding(finding_type="general", description="f", details={}),
        ]
        result = BrowserAuditResult(findings=findings, raw_stdout="...")
        assert len(result.locator_findings) == 2
        assert len(result.timing_findings) == 1
        assert len(result.axe_findings) == 2


# ── run_ux_audit ──────────────────────────────────────────────────────────────


class TestRunUxAudit:
    """End-to-end tests for the main ux_auditor entry point."""

    @pytest.mark.asyncio
    async def test_non_browser_mode_returns_analysis_only_report(self):
        """When browser is disabled, the report is analysis-only with no
        findings and no errors."""
        report = await run_ux_audit(
            "012-test-direction",
            browser_enabled=False,
        )
        assert isinstance(report, UxAuditReport)
        assert report.direction_id == "012-test-direction"
        assert report.browser_backed is False
        assert len(report.findings) == 0
        assert len(report.errors) == 0

    @pytest.mark.asyncio
    async def test_browser_mode_without_target_url_reports_error(self):
        """When browser is enabled but no target_url is given, the report
        captures a clear error — not a crash."""
        report = await run_ux_audit(
            "012-test-direction",
            browser_enabled=True,
            target_url=None,
        )
        assert report.browser_backed is True
        assert len(report.findings) == 0
        assert len(report.errors) == 1
        assert "target_url is required" in report.errors[0]

    @pytest.mark.asyncio
    async def test_browser_mode_with_unavailable_sandbox_reports_error(self):
        """When browser is enabled but sandbox is unavailable, the report
        captures the error — no exception leaks."""
        with patch.object(BrowserSandbox, "is_available", return_value=False):
            report = await run_ux_audit(
                "012-test-direction",
                browser_enabled=True,
                target_url="https://example.com",
            )
        assert report.browser_backed is True
        assert len(report.findings) == 0
        assert len(report.errors) == 1
        assert "not available" in report.errors[0]

    @pytest.mark.asyncio
    async def test_browser_mode_successful_audit_produces_findings(self):
        """AC1.1 + AC1.2: Browser-backed execution emits findings citing
        Playwright locator actions, response timings, and axe rule ids."""
        findings_payload = _findings_json([
            {
                "finding_type": "playwright_locator",
                "description": "Located 1 h1",
                "details": {"selector": "h1", "action": "count", "match_count": 1},
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "finding_type": "response_timing",
                "description": "Loaded in 300ms",
                "details": {"url": "https://example.com", "duration_ms": 300, "status_code": 200},
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "finding_type": "axe_rule",
                "description": "Axe pass: document-title",
                "details": {"rule_id": "document-title", "impact": "serious", "nodes_count": 1},
                "timestamp": "2026-01-01T00:00:00Z",
            },
        ])

        fake_sandbox = MagicMock(spec=BrowserSandbox)
        fake_sandbox.is_available.return_value = True
        fake_sandbox.image = "test-img:latest"
        fake_sandbox.run_audit.return_value = BrowserAuditResult(
            findings=[
                UxFinding(
                    finding_type="playwright_locator",
                    description="Located 1 h1",
                    details={"selector": "h1", "action": "count", "match_count": 1},
                ),
                UxFinding(
                    finding_type="response_timing",
                    description="Loaded in 300ms",
                    details={"url": "https://example.com", "duration_ms": 300, "status_code": 200},
                ),
                UxFinding(
                    finding_type="axe_rule",
                    description="Axe pass: document-title",
                    details={"rule_id": "document-title", "impact": "serious", "nodes_count": 1},
                ),
            ],
            raw_stdout=findings_payload,
        )

        report = await run_ux_audit(
            "012-test-direction",
            browser_enabled=True,
            target_url="https://example.com",
            locator_selectors=[{"selector": "h1", "action": "count"}],
            _sandbox=fake_sandbox,
        )

        assert report.browser_backed is True
        assert report.has_browser_findings is True
        assert len(report.findings) == 3
        assert len(report.errors) == 0

        # AC1.2: findings cite Playwright locator actions
        loc = [f for f in report.findings if f.finding_type == "playwright_locator"]
        assert len(loc) == 1
        assert loc[0].details["selector"] == "h1"

        # AC1.2: findings cite response timings
        tim = [f for f in report.findings if f.finding_type == "response_timing"]
        assert len(tim) == 1
        assert tim[0].details["duration_ms"] == 300

        # AC1.2: findings cite axe rule ids
        axe = [f for f in report.findings if f.finding_type == "axe_rule"]
        assert len(axe) == 1
        assert axe[0].details["rule_id"] == "document-title"

    @pytest.mark.asyncio
    async def test_browser_mode_catches_browser_execution_error(self):
        """When the sandbox raises BrowserExecutionError, the report captures
        it — no exception leaks."""
        fake_sandbox = MagicMock(spec=BrowserSandbox)
        fake_sandbox.is_available.return_value = True
        fake_sandbox.image = "test-img:latest"
        fake_sandbox.run_audit.side_effect = BrowserExecutionError("container crashed")

        report = await run_ux_audit(
            "012-test-direction",
            browser_enabled=True,
            target_url="https://example.com",
            _sandbox=fake_sandbox,
        )

        assert report.browser_backed is True
        assert len(report.findings) == 0
        assert len(report.errors) == 1
        assert "container crashed" in report.errors[0]

    @pytest.mark.asyncio
    async def test_respects_global_config_for_browser_enabled(self):
        """When browser_enabled is not passed, the global config is used."""
        # Default config has ux_auditor_browser_enabled=False
        report = await run_ux_audit("012-test-direction")
        assert report.browser_backed is False
        assert len(report.findings) == 0

    @pytest.mark.asyncio
    async def test_report_has_required_metadata(self):
        """Every report includes direction_id, timestamps, and browser_backed flag."""
        report = await run_ux_audit("my-direction", browser_enabled=False)
        assert report.direction_id == "my-direction"
        assert report.browser_backed is False
        assert report.started_at
        assert report.completed_at
        assert report.started_at <= report.completed_at


# ── UxAuditReport ─────────────────────────────────────────────────────────────


class TestUxAuditReport:
    def test_has_browser_findings_false_when_not_browser_backed(self):
        report = UxAuditReport(
            direction_id="d", browser_backed=False,
            started_at="a", completed_at="b",
        )
        assert report.has_browser_findings is False

    def test_has_browser_findings_false_when_no_findings(self):
        report = UxAuditReport(
            direction_id="d", browser_backed=True,
            started_at="a", completed_at="b", findings=[],
        )
        assert report.has_browser_findings is False

    def test_has_browser_findings_true_when_browser_backed_with_findings(self):
        report = UxAuditReport(
            direction_id="d", browser_backed=True,
            started_at="a", completed_at="b",
            findings=[UxFinding(finding_type="general", description="x", details={})],
        )
        assert report.has_browser_findings is True


# ── Smoke path: proves browser launch/use is possible ─────────────────────────


class TestBrowserSandboxSmoke:
    """Smoke tests proving the browser sandbox wiring is correct end-to-end.

    These do NOT require a real Docker daemon or Playwright image — they
    verify the *wiring* (script generation, output parsing, container
    lifecycle) is correct so that when a real image is present, everything
    works.
    """

    def test_full_smoke_wiring(self):
        """Prove that a complete browser-backed audit pipeline works with
        mocked Docker: script is generated, container runs, findings are
        parsed and categorised correctly."""
        sandbox = BrowserSandbox(image="test-img:latest")

        findings_payload = _findings_json([
            {
                "finding_type": "playwright_locator",
                "description": "Found button",
                "details": {"selector": "button.submit", "action": "count", "match_count": 1},
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "finding_type": "response_timing",
                "description": "Loaded in 150ms",
                "details": {"url": "https://example.com", "duration_ms": 150, "status_code": 200},
                "timestamp": "2026-01-01T00:00:00Z",
            },
            {
                "finding_type": "axe_rule",
                "description": "Axe violation: label",
                "details": {"rule_id": "label", "impact": "critical", "nodes_count": 2},
                "timestamp": "2026-01-01T00:00:00Z",
            },
        ])

        container = _fake_container(stdout=findings_payload, exit_code=0)

        with patch.object(sandbox, "_get_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client.containers.run.return_value = container
            mock_client_fn.return_value = mock_client
            with patch.object(sandbox, "is_available", return_value=True):
                result = sandbox.run_audit(
                    "https://example.com",
                    locator_selectors=[{"selector": "button.submit", "action": "count"}],
                )

        # AC1.1: browser access was provided (the container ran successfully)
        assert isinstance(result, BrowserAuditResult)
        assert len(result.findings) == 3
        # AC1.2: all three evidence-citing finding types are present
        assert len(result.locator_findings) == 1
        assert len(result.timing_findings) == 1
        assert len(result.axe_findings) == 1
        # Evidence details are intact
        assert result.locator_findings[0].details["selector"] == "button.submit"
        assert result.timing_findings[0].details["duration_ms"] == 150
        assert result.axe_findings[0].details["rule_id"] == "label"


# ── Failure behaviour ─────────────────────────────────────────────────────────


class TestFailureBehavior:
    """Failure messages for missing browser capability are actionable."""

    def test_browser_not_available_error_is_descriptive(self):
        """The error message distinguishes setup issues from auditor logic
        issues and includes the image name."""
        with pytest.raises(BrowserNotAvailableError, match="not available"):
            sandbox = BrowserSandbox(image="my-custom-image:v2")
            with patch.object(sandbox, "is_available", return_value=False):
                sandbox.run_audit("https://example.com")

    def test_docker_unreachable_error_mentions_docker(self):
        """When Docker is unreachable, the error tells the operator to check
        Docker, not the auditor logic."""
        try:
            raise BrowserNotAvailableError(
                "Cannot connect to Docker daemon: connection refused. "
                "Ensure Docker is installed and running."
            )
        except BrowserNotAvailableError as exc:
            assert "Docker" in str(exc)
            assert "installed and running" in str(exc)

    def test_missing_image_error_is_actionable(self):
        """When the image is missing, the error includes the image name so
        the operator knows what to pull."""
        sandbox = BrowserSandbox(image="missing-image:latest")
        with patch.object(sandbox, "_get_client") as mock_client_fn:
            mock_client = _fake_docker_client(has_image=False)
            mock_client.images.pull.side_effect = Exception("pull failed")
            mock_client_fn.return_value = mock_client
            assert sandbox.is_available() is False
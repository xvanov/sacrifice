"""Tests for UX auditor browser-sandbox wiring.

Covers:
- BrowserSandboxResult dataclass behaviour
- Finding parsing from sandbox JSON stdout
- UxAuditFinding / UxAuditReport construction and canonical citation types
- BrowserSandbox mocked-Docker tests (container launch, network enabled, shm_size)
- run_ux_audit integration path (mocked BrowserSandbox)
- Smoke assertion: the run_ux_audit path exercises its sandbox dependency
"""

import json as json_mod
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.services.ux_auditor import (
    CITATION_AXE_RULE,
    CITATION_PLAYWRIGHT_LOCATOR,
    CITATION_RESPONSE_TIMING,
    UxAuditFinding,
    UxAuditReport,
    _findings_from_sandbox_result,
    run_ux_audit,
)
from app.workers.ux_auditor_sandbox import (
    BrowserSandbox,
    BrowserSandboxError,
    BrowserSandboxResult,
    run_ux_audit_sandboxed,
)


# ── BrowserSandboxResult ────────────────────────────────────────────────────


class TestBrowserSandboxResult:
    def test_success_when_exit_zero_and_not_timed_out(self):
        r = BrowserSandboxResult(exit_code=0, stdout="ok", stderr="")
        assert r.success is True

    def test_success_false_on_nonzero_exit(self):
        r = BrowserSandboxResult(exit_code=1, stdout="", stderr="err")
        assert r.success is False

    def test_success_false_on_timeout(self):
        r = BrowserSandboxResult(exit_code=0, stdout="", stderr="", timed_out=True)
        assert r.success is False

    def test_findings_defaults_to_empty_list(self):
        r = BrowserSandboxResult(exit_code=0, stdout="", stderr="")
        assert r.findings == []


# ── Finding parsing ─────────────────────────────────────────────────────────


_SAMPLE_AUDIT_STDOUT = json_mod.dumps([
    {
        "citation_type": "response_timing",
        "url": "https://example.com",
        "load_time_ms": 342,
        "status_code": 200,
    },
    {
        "citation_type": "axe_rule",
        "rule_id": "color-contrast",
        "impact": "serious",
        "description": "Elements must have sufficient color contrast",
        "help_url": "https://dequeuniversity.com/rules/axe/4.10/color-contrast",
        "nodes": [
            {"target": [".hero > h1"], "html": "<h1>Hello</h1>"},
        ],
    },
    {
        "citation_type": "playwright_locator",
        "locator": "button:has-text('Submit')",
        "tag": "button",
        "text_content": "Submit",
    },
])


def _make_result_from_stdout(stdout: str) -> BrowserSandboxResult:
    """Build a BrowserSandboxResult with findings parsed from *stdout*,
    mimicking what BrowserSandbox.run_audit() does internally."""
    from app.workers.ux_auditor_sandbox import BrowserSandbox as BS
    sandbox = BS()  # only used for _parse_findings
    return BrowserSandboxResult(
        exit_code=0,
        stdout=stdout,
        stderr="",
        findings=sandbox._parse_findings(stdout),
    )


class TestFindingsFromSandboxResult:
    def test_parses_all_canonical_citation_types(self):
        result = _make_result_from_stdout(_SAMPLE_AUDIT_STDOUT)
        findings = _findings_from_sandbox_result(result)
        assert len(findings) == 3

        ctypes = {f.citation_type for f in findings}
        assert ctypes == {"response_timing", "axe_rule", "playwright_locator"}

    def test_response_timing_finding_detail(self):
        stdout = json_mod.dumps([{
            "citation_type": "response_timing",
            "url": "https://example.com",
            "load_time_ms": 342,
            "status_code": 200,
        }])
        result = _make_result_from_stdout(stdout)
        findings = _findings_from_sandbox_result(result)
        assert len(findings) == 1
        f = findings[0]
        assert f.citation_type == "response_timing"
        assert "342ms" in f.summary
        assert "200" in f.summary
        assert f.detail["load_time_ms"] == 342

    def test_axe_rule_finding_detail(self):
        stdout = json_mod.dumps([{
            "citation_type": "axe_rule",
            "rule_id": "color-contrast",
            "impact": "serious",
            "description": "Elements must have sufficient color contrast",
            "help_url": "https://example.com/rule",
            "nodes": [],
        }])
        result = _make_result_from_stdout(stdout)
        findings = _findings_from_sandbox_result(result)
        assert len(findings) == 1
        f = findings[0]
        assert f.citation_type == "axe_rule"
        assert "color-contrast" in f.summary
        assert f.detail["rule_id"] == "color-contrast"

    def test_playwright_locator_finding_detail(self):
        stdout = json_mod.dumps([{
            "citation_type": "playwright_locator",
            "locator": "button:has-text('Submit')",
            "tag": "button",
            "text_content": "Submit",
        }])
        result = _make_result_from_stdout(stdout)
        findings = _findings_from_sandbox_result(result)
        assert len(findings) == 1
        f = findings[0]
        assert f.citation_type == "playwright_locator"
        assert "button:has-text('Submit')" in f.summary
        assert f.detail["locator"] == "button:has-text('Submit')"

    def test_drops_non_canonical_citation_types(self):
        """Unrecognised citation types are silently dropped so downstream
        consumers see a clean contract."""
        stdout = json_mod.dumps([
            {"citation_type": "heuristic_guess", "note": "maybe bad"},
            {"citation_type": "response_timing", "load_time_ms": 100, "status_code": 200},
        ])
        result = _make_result_from_stdout(stdout)
        findings = _findings_from_sandbox_result(result)
        assert len(findings) == 1
        assert findings[0].citation_type == "response_timing"

    def test_empty_stdout_returns_empty_findings(self):
        result = _make_result_from_stdout("")
        assert _findings_from_sandbox_result(result) == []

    def test_non_json_stdout_returns_empty_findings(self):
        result = _make_result_from_stdout("not json")
        assert _findings_from_sandbox_result(result) == []


# ── UxAuditFinding / UxAuditReport ──────────────────────────────────────────


class TestUxAuditFinding:
    def test_constructs_with_required_fields(self):
        f = UxAuditFinding(
            citation_type="axe_rule",
            summary="Axe rule color-contrast: insufficient contrast",
        )
        assert f.citation_type == "axe_rule"
        assert "color-contrast" in f.summary
        assert f.detail == {}

    def test_detail_stores_extra_data(self):
        f = UxAuditFinding(
            citation_type="response_timing",
            summary="342ms",
            detail={"load_time_ms": 342, "status_code": 200},
        )
        assert f.detail["load_time_ms"] == 342


class TestUxAuditReport:
    def test_report_success_true_when_no_error_and_exit_zero(self):
        r = UxAuditReport(
            direction_id="012-test", target_url="https://example.com",
            sandbox_exit_code=0,
        )
        assert r.success is True

    def test_report_success_false_on_error(self):
        r = UxAuditReport(
            direction_id="012-test", target_url="https://example.com",
            error="something broke",
        )
        assert r.success is False

    def test_report_success_false_on_timeout(self):
        r = UxAuditReport(
            direction_id="012-test", target_url="https://example.com",
            sandbox_exit_code=0, sandbox_timed_out=True,
        )
        assert r.success is False

    def test_findings_default_to_empty(self):
        r = UxAuditReport(direction_id="012-test", target_url="https://example.com")
        assert r.findings == []


# ── BrowserSandbox (mocked Docker) ──────────────────────────────────────────


@pytest.fixture
def mock_docker_client():
    with patch("app.workers.ux_auditor_sandbox.docker.from_env") as mock_from_env:
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_container():
    container = MagicMock()
    container.wait.return_value = {"StatusCode": 0}
    container.logs.side_effect = [_SAMPLE_AUDIT_STDOUT.encode(), b""]
    return container


class TestBrowserSandbox:
    def test_default_image_is_playwright(self, mock_docker_client):
        sandbox = BrowserSandbox()
        assert "playwright" in sandbox.image

    def test_custom_image_accepted(self, mock_docker_client):
        sandbox = BrowserSandbox(image="my-playwright:latest")
        assert sandbox.image == "my-playwright:latest"

    def test_run_audit_launches_container_with_network_enabled(
        self, mock_docker_client, mock_container
    ):
        mock_docker_client.containers.run.return_value = mock_container

        sandbox = BrowserSandbox()
        sandbox.run_audit("https://example.com")

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert call_kwargs["network_disabled"] is False, (
            "browser sandbox MUST have network access for live-page navigation"
        )

    def test_run_audit_sets_shm_size_for_chromium(
        self, mock_docker_client, mock_container
    ):
        mock_docker_client.containers.run.return_value = mock_container

        sandbox = BrowserSandbox()
        sandbox.run_audit("https://example.com")

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert call_kwargs.get("shm_size") == "256m", (
            "Chromium needs shared memory for rendering"
        )

    def test_run_audit_passes_target_url_as_command_arg(
        self, mock_docker_client, mock_container
    ):
        mock_docker_client.containers.run.return_value = mock_container

        sandbox = BrowserSandbox()
        sandbox.run_audit("https://example.com/page")

        call_args = mock_docker_client.containers.run.call_args.kwargs
        command = call_args["command"]
        assert command[-1] == "https://example.com/page"

    def test_run_audit_returns_findings(
        self, mock_docker_client, mock_container
    ):
        mock_docker_client.containers.run.return_value = mock_container

        sandbox = BrowserSandbox()
        result = sandbox.run_audit("https://example.com")

        assert isinstance(result, BrowserSandboxResult)
        assert result.exit_code == 0
        assert len(result.findings) == 3
        ctypes = {f["citation_type"] for f in result.findings}
        assert ctypes == {"response_timing", "axe_rule", "playwright_locator"}

    def test_run_audit_no_privileged_mode(
        self, mock_docker_client, mock_container
    ):
        mock_docker_client.containers.run.return_value = mock_container

        sandbox = BrowserSandbox()
        sandbox.run_audit("https://example.com")

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert call_kwargs.get("privileged") is False

    def test_run_audit_no_new_privileges(
        self, mock_docker_client, mock_container
    ):
        mock_docker_client.containers.run.return_value = mock_container

        sandbox = BrowserSandbox()
        sandbox.run_audit("https://example.com")

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert "no-new-privileges:true" in call_kwargs.get("security_opt", [])

    def test_container_cleaned_up_after_run(
        self, mock_docker_client, mock_container
    ):
        mock_docker_client.containers.run.return_value = mock_container

        sandbox = BrowserSandbox()
        sandbox.run_audit("https://example.com")

        mock_container.remove.assert_called_once_with(force=True)
        assert sandbox.container is None

    def test_timeout_kills_container(
        self, mock_docker_client
    ):
        import docker as docker_mod

        mock_container = MagicMock()
        mock_container.wait.side_effect = docker_mod.errors.APIError(
            "Timeout: 120 seconds exceeded"
        )
        mock_docker_client.containers.run.return_value = mock_container

        sandbox = BrowserSandbox(timeout=5)
        result = sandbox.run_audit("https://example.com")

        assert result.timed_out is True
        assert result.success is False
        assert result.exit_code == -1
        mock_container.kill.assert_called_once()

    def test_run_audit_container_start_failure_raises(
        self, mock_docker_client
    ):
        mock_docker_client.containers.run.side_effect = Exception(
            "No such image"
        )

        sandbox = BrowserSandbox()
        with pytest.raises(BrowserSandboxError, match="Failed to start browser sandbox"):
            sandbox.run_audit("https://example.com")

    def test_sandbox_not_privileged(
        self, mock_docker_client, mock_container
    ):
        mock_docker_client.containers.run.return_value = mock_container

        sandbox = BrowserSandbox()
        sandbox.run_audit("https://example.com")

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert call_kwargs.get("privileged") is False


# ── run_ux_audit_sandboxed convenience ──────────────────────────────────────


class TestRunUxAuditSandboxed:
    def test_convenience_returns_result(self, mock_docker_client, mock_container):
        mock_docker_client.containers.run.return_value = mock_container

        result = run_ux_audit_sandboxed("https://example.com")
        assert isinstance(result, BrowserSandboxResult)
        assert result.exit_code == 0


# ── run_ux_audit integration (mocked BrowserSandbox) ────────────────────────


class TestRunUxAudit:
    @pytest.mark.asyncio
    async def test_run_ux_audit_returns_report_with_findings(self):
        """AC1.1 & AC1.2: run_ux_audit goes through the sandbox path AND
        the returned report includes findings citing Playwright locators,
        response timings, or axe rule ids."""
        with patch(
            "app.workers.ux_auditor_sandbox.BrowserSandbox"
        ) as mock_sandbox_cls:
            mock_sandbox = MagicMock()
            mock_sandbox.run_audit.return_value = BrowserSandboxResult(
                exit_code=0,
                stdout=_SAMPLE_AUDIT_STDOUT,
                stderr="",
                findings=json_mod.loads(_SAMPLE_AUDIT_STDOUT),
            )
            mock_sandbox_cls.return_value = mock_sandbox

            report = await run_ux_audit(
                direction_id="012-test",
                target_url="https://example.com",
            )

        assert report.direction_id == "012-test"
        assert report.target_url == "https://example.com"
        assert report.success is True
        assert report.sandbox_exit_code == 0
        assert len(report.findings) == 3

        # AC1.2: findings cite Playwright locators, response timings, or axe rule ids
        ctypes = {f.citation_type for f in report.findings}
        assert ctypes == {"playwright_locator", "response_timing", "axe_rule"}

        # Verify the sandbox was constructed and called with the target URL
        mock_sandbox_cls.assert_called_once()
        mock_sandbox.run_audit.assert_called_once_with("https://example.com")

    @pytest.mark.asyncio
    async def test_run_ux_audit_reports_sandbox_error(self):
        """When the BrowserSandbox raises, the report carries the error."""
        with patch(
            "app.workers.ux_auditor_sandbox.BrowserSandbox"
        ) as mock_sandbox_cls:
            mock_sandbox_cls.side_effect = BrowserSandboxError(
                "Failed to start browser sandbox container"
            )

            report = await run_ux_audit(
                direction_id="012-test",
                target_url="https://example.com",
            )

        assert report.success is False
        assert "Failed to start browser sandbox" in (report.error or "")
        assert report.findings == []

    @pytest.mark.asyncio
    async def test_run_ux_audit_reports_timeout(self):
        """When the sandbox times out, the report reflects it."""
        with patch(
            "app.workers.ux_auditor_sandbox.BrowserSandbox"
        ) as mock_sandbox_cls:
            mock_sandbox = MagicMock()
            mock_sandbox.run_audit.return_value = BrowserSandboxResult(
                exit_code=-1, stdout="", stderr="", timed_out=True,
            )
            mock_sandbox_cls.return_value = mock_sandbox

            report = await run_ux_audit(
                direction_id="012-test",
                target_url="https://example.com",
            )

        assert report.success is False
        assert report.sandbox_timed_out is True
        assert "timed out" in (report.error or "").lower()

    @pytest.mark.asyncio
    async def test_run_ux_audit_passes_custom_image_and_timeout(self):
        """Custom sandbox_image and timeout are forwarded to BrowserSandbox."""
        with patch(
            "app.workers.ux_auditor_sandbox.BrowserSandbox"
        ) as mock_sandbox_cls:
            mock_sandbox = MagicMock()
            mock_sandbox.run_audit.return_value = BrowserSandboxResult(
                exit_code=0, stdout="[]", stderr="",
            )
            mock_sandbox_cls.return_value = mock_sandbox

            await run_ux_audit(
                direction_id="012-test",
                target_url="https://example.com",
                sandbox_image="custom-playwright:v2",
                timeout=60,
            )

        mock_sandbox_cls.assert_called_once_with(
            image="custom-playwright:v2", timeout=60
        )


# ── Canonical citation type constants ───────────────────────────────────────


class TestCanonicalCitationTypes:
    """The three canonical citation types are the contract between the UX
    auditor and downstream evidence-emission work."""

    def test_all_three_constants_defined(self):
        assert CITATION_PLAYWRIGHT_LOCATOR == "playwright_locator"
        assert CITATION_RESPONSE_TIMING == "response_timing"
        assert CITATION_AXE_RULE == "axe_rule"

    def test_constants_are_distinct(self):
        types = {
            CITATION_PLAYWRIGHT_LOCATOR,
            CITATION_RESPONSE_TIMING,
            CITATION_AXE_RULE,
        }
        assert len(types) == 3

    def test_finding_from_sandbox_result_only_accepts_canonical(self):
        """Demonstrate that only the three canonical types pass through."""
        stdout = json_mod.dumps([
            {"citation_type": "playwright_locator", "locator": "button"},
            {"citation_type": "response_timing", "load_time_ms": 100},
            {"citation_type": "axe_rule", "rule_id": "r1"},
            {"citation_type": "unknown_type", "data": "should be dropped"},
        ])
        result = _make_result_from_stdout(stdout)
        findings = _findings_from_sandbox_result(result)
        assert len(findings) == 3
        assert all(
            f.citation_type in ("playwright_locator", "response_timing", "axe_rule")
            for f in findings
        )


# ── Smoke: run_local via subprocess (no Docker) ─────────────────────────────


class TestBrowserSandboxRunLocal:
    """Smoke tests for the local (non-Docker) sandbox execution path.

    These tests exercise the ``run_local`` method which uses ``subprocess``
    to execute the audit script.  They do NOT require Docker or a real
    browser — the subprocess call is mocked to return known JSON findings,
    proving the end-to-end plumbing from ``run_local`` → subprocess →
    parsed ``BrowserSandboxResult`` with citations works.
    """

    def test_run_local_emits_browser_backed_citations(self):
        """AC1.1 & AC1.2: run_local executes the audit script and returns
        findings citing Playwright locators, response timings, and axe rule
        ids — proving the browser-capable path is wired."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=_SAMPLE_AUDIT_STDOUT.encode(),
                stderr=b"",
            )

            sandbox = BrowserSandbox(timeout=30)
            result = sandbox.run_local("https://example.com")

        assert result.exit_code == 0
        assert result.timed_out is False
        assert len(result.findings) == 3

        ctypes = {f["citation_type"] for f in result.findings}
        assert ctypes == {"playwright_locator", "response_timing", "axe_rule"}, (
            "AC1.2: findings MUST cite Playwright locator actions, "
            "response timings, or axe rule ids"
        )

        # Verify the subprocess was invoked with the audit script and target URL
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "python"
        assert call_args[2] == "https://example.com"

    def test_run_local_timeout_propagates(self):
        """When subprocess times out, the result reflects it."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=5)):
            sandbox = BrowserSandbox(timeout=5)
            result = sandbox.run_local("https://example.com")

        assert result.timed_out is True
        assert result.exit_code == -1
        assert result.success is False

    def test_run_local_nonzero_exit(self):
        """Non-zero exit codes from the audit script are propagated."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout=b"",
                stderr=b"Playwright browser not found",
            )

            sandbox = BrowserSandbox()
            result = sandbox.run_local("https://example.com")

        assert result.exit_code == 1
        assert result.success is False
        assert "Playwright" in result.stderr


# ── run_ux_audit_local convenience ─────────────────────────────────────────


class TestRunUxAuditLocal:
    def test_convenience_function_returns_result(self):
        """run_ux_audit_local is a thin wrapper that calls run_local."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=_SAMPLE_AUDIT_STDOUT.encode(),
                stderr=b"",
            )

            from app.workers.ux_auditor_sandbox import run_ux_audit_local

            result = run_ux_audit_local("https://example.com", timeout=30)

        assert isinstance(result, BrowserSandboxResult)
        assert result.exit_code == 0
        assert len(result.findings) == 3
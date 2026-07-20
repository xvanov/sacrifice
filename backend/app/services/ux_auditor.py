"""UX Auditor service — browser-backed UX audit for direction artifacts.

Provides the runtime/sandbox layer that lets ``ux_auditor`` executions launch
and control a live browser session (Playwright), with validation hooks that
make the browser-backed execution path observable to downstream
evidence-emission work.

This is the **broad-read infra** slice: it delivers browser capability,
availability detection, and a findings envelope.  Downstream stories own
the final evidence schema and rendering.
"""

from __future__ import annotations

import json as json_mod
import shlex
import time as time_mod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import docker
from docker.errors import DockerException, ImageNotFound, NotFound

from app.config import settings


# ── Errors ────────────────────────────────────────────────────────────────────


class BrowserSandboxError(RuntimeError):
    """Raised when the browser sandbox cannot be initialised or used."""


class BrowserNotAvailableError(BrowserSandboxError):
    """Raised when browser capability is unavailable in the runtime.

    This is an actionable setup/runtime error, not an auditor logic error.
    The message distinguishes missing Docker, missing image, and image-pull
    failures so operators can triage quickly.
    """


class BrowserExecutionError(BrowserSandboxError):
    """Raised when the browser sandbox runs but the browser process fails."""


# ── Finding record ────────────────────────────────────────────────────────────


@dataclass
class UxFinding:
    """A single UX audit finding emitted during a browser-backed run.

    This is the **prerequisite shape** for downstream evidence-citing.
    Downstream stories will add schemas, rendering, and persistence; here
    we only guarantee that the fields needed for evidence citation exist.
    """

    finding_type: str
    """One of ``playwright_locator``, ``response_timing``, ``axe_rule``,
    ``accessibility``, or ``general``."""

    description: str
    """Human-readable description of the finding."""

    details: dict = field(default_factory=dict)
    """Arbitrary extra data. For locator findings this SHOULD include
    ``selector`` and ``action``; for timing findings it SHOULD include
    ``url`` and ``duration_ms``; for axe findings it SHOULD include
    ``rule_id`` and ``impact``."""

    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ── Browser sandbox ───────────────────────────────────────────────────────────


# Inline script that the Playwright container runs to:
# 1. Navigate to a target URL
# 2. Collect locator-action evidence, response timing, and run axe-core
# 3. Print a JSON findings envelope to stdout
_BROWSER_SCRIPT_TEMPLATE = """\
const {{ chromium }} = require("playwright");
(async () => {{
    const browser = await chromium.launch({{ headless: true }});
    const context = await browser.newContext();
    const page = await context.newPage();

    const findings = [];
    const start_ms = Date.now();

    // ── Navigate ──────────────────────────────────────────────────────
    let response = null;
    try {{
        response = await page.goto("{target_url}", {{ waitUntil: "networkidle", timeout: {timeout_ms} }});
    }} catch (navErr) {{
        findings.push({{
            finding_type: "general",
            description: "Navigation failed: " + navErr.message,
            details: {{ url: "{target_url}", error: navErr.message }},
            timestamp: new Date().toISOString()
        }});
        console.log(JSON.stringify({{ findings }}));
        await browser.close();
        return;
    }}

    // ── Response timing ───────────────────────────────────────────────
    const elapsed_ms = Date.now() - start_ms;
    findings.push({{
        finding_type: "response_timing",
        description: "Page loaded in " + elapsed_ms + " ms",
        details: {{
            url: "{target_url}",
            duration_ms: elapsed_ms,
            status_code: response ? response.status() : null
        }},
        timestamp: new Date().toISOString()
    }});

    // ── Locator actions ───────────────────────────────────────────────
    const locator_targets = {locator_targets_json};
    for (const tgt of locator_targets) {{
        try {{
            const loc = page.locator(tgt.selector);
            const count = await loc.count();
            findings.push({{
                finding_type: "playwright_locator",
                description: "Located " + count + " element(s) matching '" + tgt.selector + "'",
                details: {{ selector: tgt.selector, action: tgt.action || "count", match_count: count }},
                timestamp: new Date().toISOString()
            }});
        }} catch (locErr) {{
            findings.push({{
                finding_type: "playwright_locator",
                description: "Locator error for '" + tgt.selector + "': " + locErr.message,
                details: {{ selector: tgt.selector, action: tgt.action || "count", error: locErr.message }},
                timestamp: new Date().toISOString()
            }});
        }}
    }}

    // ── Axe-core accessibility scan ───────────────────────────────────
    try {{
        await page.addScriptTag({{ path: "/opt/axe/axe.min.js" }});
        const axeResults = await page.evaluate(async () => {{
            if (typeof axe === "undefined") return null;
            return await axe.run();
        }});
        if (axeResults) {{
            for (const violation of (axeResults.violations || [])) {{
                findings.push({{
                    finding_type: "axe_rule",
                    description: "Axe violation: " + (violation.help || violation.id),
                    details: {{
                        rule_id: violation.id,
                        impact: violation.impact || "unknown",
                        help_url: violation.helpUrl || "",
                        nodes_count: (violation.nodes || []).length
                    }},
                    timestamp: new Date().toISOString()
                }});
            }}
            for (const pass of (axeResults.passes || [])) {{
                findings.push({{
                    finding_type: "axe_rule",
                    description: "Axe pass: " + (pass.help || pass.id),
                    details: {{
                        rule_id: pass.id,
                        impact: pass.impact || "unknown",
                        nodes_count: (pass.nodes || []).length
                    }},
                    timestamp: new Date().toISOString()
                }});
            }}
        }}
    }} catch (axeErr) {{
        findings.push({{
            finding_type: "general",
            description: "Axe-core scan failed: " + axeErr.message,
            details: {{ error: axeErr.message }},
            timestamp: new Date().toISOString()
        }});
    }}

    console.log(JSON.stringify({{ findings }}));
    await browser.close();
}})();
"""


def _build_browser_script(
    target_url: str,
    *,
    locator_selectors: list[dict] | None = None,
    timeout_ms: int = 30_000,
) -> str:
    """Build the inline Playwright script for a browser audit run.

    Args:
        target_url: The URL to navigate to.
        locator_selectors: List of ``{"selector": "...", "action": "..."}``
            dicts for locator checks.
        timeout_ms: Navigation timeout in milliseconds.
    """
    locator_json = json_mod.dumps(locator_selectors or [])
    return _BROWSER_SCRIPT_TEMPLATE.format(
        target_url=target_url,
        locator_targets_json=locator_json,
        timeout_ms=timeout_ms,
    )


class BrowserSandbox:
    """Sandbox that runs a Playwright browser inside a Docker container.

    Uses the official Microsoft Playwright image so Chromium, Firefox, and
    WebKit are pre-installed.  The container runs with network access (unlike
    the dev_sandbox DockerSandbox) so it can reach target URLs.

    Typical usage::

        sandbox = BrowserSandbox()
        if not sandbox.is_available():
            raise BrowserNotAvailableError(...)
        result = sandbox.run_audit(target_url="https://example.com")
        for finding in result.findings:
            print(finding.finding_type, finding.description)
    """

    def __init__(
        self,
        *,
        image: str | None = None,
        timeout: int | None = None,
    ):
        self._image = image or settings.ux_auditor_browser_image
        self._timeout = timeout or settings.ux_auditor_browser_timeout
        self._client: docker.DockerClient | None = None

    @property
    def image(self) -> str:
        return self._image

    @property
    def timeout(self) -> int:
        return self._timeout

    def _get_client(self) -> docker.DockerClient:
        if self._client is None:
            try:
                self._client = docker.from_env()
            except DockerException as exc:
                raise BrowserNotAvailableError(
                    f"Cannot connect to Docker daemon: {exc}. "
                    f"Ensure Docker is installed and running."
                ) from exc
        return self._client

    def is_available(self) -> bool:
        """Check whether the browser sandbox can be used.

        Returns ``True`` when Docker is reachable AND the configured
        Playwright image is present (or pullable).  Returns ``False``
        when the runtime lacks Docker or the image cannot be obtained.
        """
        try:
            client = self._get_client()
            try:
                client.images.get(self._image)
            except ImageNotFound:
                # Try pulling — if this fails, availability is false
                try:
                    client.images.pull(self._image)
                except Exception:
                    return False
            return True
        except BrowserNotAvailableError:
            return False

    def run_audit(
        self,
        target_url: str,
        *,
        locator_selectors: list[dict] | None = None,
        timeout_ms: int = 30_000,
    ) -> BrowserAuditResult:
        """Run a browser audit against a target URL.

        Returns a ``BrowserAuditResult`` with parsed findings.

        Raises:
            BrowserNotAvailableError: If Docker or the browser image are
                unavailable.
            BrowserExecutionError: If the container runs but produces
                unparseable output or exits non-zero for a non-setup reason.
        """
        if not self.is_available():
            raise BrowserNotAvailableError(
                f"Browser sandbox is not available. "
                f"Image '{self._image}' could not be found or pulled. "
                f"Ensure Docker is running and the image is accessible."
            )

        script = _build_browser_script(
            target_url,
            locator_selectors=locator_selectors,
            timeout_ms=timeout_ms,
        )

        client = self._get_client()
        container = None
        try:
            container = client.containers.run(
                image=self._image,
                command=["node", "-e", script],
                detach=True,
                remove=False,
                stdout=True,
                stderr=True,
            )
        except Exception as exc:
            raise BrowserExecutionError(
                f"Failed to start browser container: {exc}"
            ) from exc

        try:
            wait_result = container.wait(timeout=self._timeout)
            exit_code = wait_result.get("StatusCode", -1)

            raw_logs = container.logs(stdout=True, stderr=False)
            stdout = raw_logs.decode("utf-8", errors="replace") if raw_logs else ""

            if exit_code != 0:
                raw_err = container.logs(stdout=False, stderr=True)
                stderr = raw_err.decode("utf-8", errors="replace") if raw_err else ""
                raise BrowserExecutionError(
                    f"Browser audit container exited with code {exit_code}. "
                    f"stderr: {stderr[:500]}"
                )

            return _parse_audit_output(stdout)

        except BrowserExecutionError:
            raise
        except docker.errors.APIError as exc:
            error_str = str(exc).lower()
            if "timeout" in error_str:
                try:
                    container.kill()
                except Exception:
                    pass
                raise BrowserExecutionError(
                    f"Browser audit timed out after {self._timeout}s"
                ) from exc
            raise BrowserExecutionError(
                f"Browser audit container error: {exc}"
            ) from exc
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass


@dataclass
class BrowserAuditResult:
    """Result of a browser-backed UX audit run."""

    findings: list[UxFinding] = field(default_factory=list)
    raw_stdout: str = ""

    @property
    def locator_findings(self) -> list[UxFinding]:
        return [f for f in self.findings if f.finding_type == "playwright_locator"]

    @property
    def timing_findings(self) -> list[UxFinding]:
        return [f for f in self.findings if f.finding_type == "response_timing"]

    @property
    def axe_findings(self) -> list[UxFinding]:
        return [f for f in self.findings if f.finding_type == "axe_rule"]


def _parse_audit_output(stdout: str) -> BrowserAuditResult:
    """Parse the JSON findings envelope from the browser script stdout."""
    findings: list[UxFinding] = []
    # The script prints a single JSON object; use a balanced-brace extractor
    # to find the outermost ``{ ... }`` block.
    try:
        json_str = _extract_json_object(stdout)
        if json_str is None:
            raise ValueError("No JSON object found in browser script output")

        parsed = json_mod.loads(json_str)
        raw_findings = parsed.get("findings", [])
        for f in raw_findings:
            findings.append(UxFinding(
                finding_type=f.get("finding_type", "general"),
                description=f.get("description", ""),
                details=f.get("details", {}),
                timestamp=f.get("timestamp", datetime.now(timezone.utc).isoformat()),
            ))
    except (json_mod.JSONDecodeError, ValueError) as exc:
        raise BrowserExecutionError(
            f"Failed to parse browser audit output: {exc}. "
            f"Raw stdout (first 500 chars): {stdout[:500]}"
        ) from exc

    return BrowserAuditResult(findings=findings, raw_stdout=stdout)


def _extract_json_object(text: str) -> str | None:
    """Extract the first balanced ``{...}`` JSON object from *text*.

    Returns the substring (inclusive of the outer braces), or ``None`` if
    no balanced object is found.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


# ── UX Auditor entry point ────────────────────────────────────────────────────


@dataclass
class UxAuditReport:
    """Top-level report produced by a UX auditor run.

    This is the **observability envelope** that downstream evidence-emission
    stories will consume.  It records whether the run was browser-backed,
    what findings were produced, and any errors.
    """

    direction_id: str
    browser_backed: bool
    started_at: str
    completed_at: str
    findings: list[UxFinding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_browser_findings(self) -> bool:
        """True when at least one browser-backed finding was produced."""
        return self.browser_backed and len(self.findings) > 0


async def run_ux_audit(
    direction_id: str,
    *,
    target_url: str | None = None,
    locator_selectors: list[dict] | None = None,
    browser_enabled: bool | None = None,
    _sandbox: BrowserSandbox | None = None,
) -> UxAuditReport:
    """Run a UX audit for a direction.

    This is the **primary entry point** for ux_auditor executions.  When
    ``browser_enabled`` is True (or when the global config enables it),
    the audit launches a live browser session via ``BrowserSandbox`` and
    collects locator, timing, and accessibility findings.  When browser
    mode is disabled, the audit returns an analysis-only report with no
    findings — the downstream story adds the analysis logic.

    Args:
        direction_id: The direction to audit.
        target_url: URL to audit. Required when browser is enabled.
        locator_selectors: Playwright locator descriptors for the audit.
        browser_enabled: Override the global ``ux_auditor_browser_enabled``
            setting.  Pass ``False`` to force analysis-only mode.
        _sandbox: Test injection seam for ``BrowserSandbox``.

    Returns:
        ``UxAuditReport`` with findings and metadata.
    """
    started_at = datetime.now(timezone.utc).isoformat()

    use_browser = browser_enabled if browser_enabled is not None else settings.ux_auditor_browser_enabled

    if not use_browser:
        return UxAuditReport(
            direction_id=direction_id,
            browser_backed=False,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
            findings=[],
            errors=[],
        )

    # ── Browser-backed path ──────────────────────────────────────────────
    sandbox = _sandbox if _sandbox is not None else BrowserSandbox()

    if not target_url:
        return UxAuditReport(
            direction_id=direction_id,
            browser_backed=True,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
            findings=[],
            errors=["target_url is required for browser-backed audit but was None"],
        )

    errors: list[str] = []
    findings: list[UxFinding] = []

    try:
        if not sandbox.is_available():
            return UxAuditReport(
                direction_id=direction_id,
                browser_backed=True,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
                findings=[],
                errors=[
                    f"Browser sandbox is not available. "
                    f"Image '{sandbox.image}' could not be found or pulled. "
                    f"Ensure Docker is running and the image is accessible."
                ],
            )

        result = sandbox.run_audit(
            target_url,
            locator_selectors=locator_selectors,
        )
        findings = result.findings

    except BrowserNotAvailableError as exc:
        errors.append(str(exc))
    except BrowserExecutionError as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f"Unexpected error during browser audit: {exc}")

    return UxAuditReport(
        direction_id=direction_id,
        browser_backed=True,
        started_at=started_at,
        completed_at=datetime.now(timezone.utc).isoformat(),
        findings=findings,
        errors=errors,
    )
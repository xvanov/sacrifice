"""Browser-capable sandbox runtime for UX auditor runs.

Provides ``BrowserSandbox``, a Docker-backed execution environment that
launches containers with Playwright browsers pre-installed and network
access enabled so the UX auditor can navigate live pages, collect
accessibility findings, and capture response timings.
"""

from __future__ import annotations

import json as json_mod
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import docker


class BrowserSandboxError(Exception):
    """Raised when a browser-sandbox operation fails."""


@dataclass
class BrowserSandboxResult:
    """Result from a browser-capable sandbox run."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    findings: list[dict] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


# Lightweight Playwright audit script that runs *inside* the container.
# It navigates to a target URL, runs axe-core accessibility checks,
# captures response timings, and emits JSON findings on stdout.
_AUDIT_SCRIPT = r"""
import json, sys, time
from playwright.sync_api import sync_playwright

def audit(url: str) -> list[dict]:
    findings = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        t0 = time.monotonic()
        resp = page.goto(url, wait_until="load", timeout=30_000)
        load_ms = int((time.monotonic() - t0) * 1000)

        findings.append({
            "citation_type": "response_timing",
            "url": url,
            "load_time_ms": load_ms,
            "status_code": resp.status if resp else None,
        })

        # Inject axe-core and collect accessibility violations
        try:
            axe_path = "/opt/axe-core/axe.min.js"
            with open(axe_path, "r") as fh:
                axe_js = fh.read()
            page.evaluate(axe_js)
            axe_results = page.evaluate("() => axe.run()")
            for violation in axe_results.get("violations", []):
                findings.append({
                    "citation_type": "axe_rule",
                    "rule_id": violation.get("id", ""),
                    "impact": violation.get("impact", ""),
                    "description": violation.get("description", ""),
                    "help_url": violation.get("helpUrl", ""),
                    "nodes": [
                        {"target": n.get("target", []), "html": n.get("html", "")[:200]}
                        for n in violation.get("nodes", [])
                    ][:5],
                })
        except Exception:
            pass  # axe not available — not every image needs it

        # Collect visible locator references from key interactive elements
        for selector in ["button", "a", "input", "select", "textarea"]:
            try:
                elements = page.locator(selector).all()
                for el in elements[:5]:
                    tag = el.evaluate("el => el.tagName.toLowerCase()")
                    text = el.evaluate("el => (el.textContent || '').trim().slice(0, 80)")
                    locator_ref = f"{selector}:has-text('{text}')" if text else selector
                    findings.append({
                        "citation_type": "playwright_locator",
                        "locator": locator_ref,
                        "tag": tag,
                        "text_content": text,
                    })
            except Exception:
                pass

        browser.close()
    return findings

if __name__ == "__main__":
    url = sys.argv[1]
    results = audit(url)
    json.dump(results, sys.stdout)
"""


class BrowserSandbox:
    """Docker-backed sandbox that provides browser access for UX auditor runs.

    Uses a Playwright-capable image with Chromium and axe-core available
    so the auditor can navigate pages, collect locator references,
    response timings, and accessibility rule findings.
    """

    DEFAULT_IMAGE = "mcr.microsoft.com/playwright:latest"

    def __init__(
        self,
        image: str | None = None,
        memory_limit: str = "2g",
        cpu_limit: float = 1.0,
        timeout: int = 120,
    ):
        self.image = image or self.DEFAULT_IMAGE
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.timeout = timeout
        self.container = None
        self._client = docker.from_env()

    def run_audit(self, target_url: str) -> BrowserSandboxResult:
        """Run the browser audit script against *target_url*.

        The audit script is written to a temp file, mounted into the
        container, and executed with ``python /audit/audit.py <url>``.
        Findings are parsed from the JSON written to stdout.
        """
        import tempfile

        with tempfile.TemporaryDirectory(prefix="ux_audit_") as tmpdir:
            tmp = Path(tmpdir)
            audit_py = tmp / "audit.py"
            audit_py.write_text(_AUDIT_SCRIPT)

            volumes = {str(tmp): {"bind": "/audit", "mode": "ro"}}

            try:
                container = self._client.containers.run(
                    image=self.image,
                    command=["python", "/audit/audit.py", target_url],
                    mem_limit=self.memory_limit,
                    nano_cpus=int(self.cpu_limit * 1e9),
                    network_disabled=False,
                    detach=True,
                    remove=False,
                    privileged=False,
                    security_opt=["no-new-privileges:true"],
                    volumes=volumes,
                    shm_size="256m",  # Chromium needs /dev/shm
                )
            except Exception:
                self.container = None
                raise BrowserSandboxError(
                    f"Failed to start browser sandbox container for {target_url}"
                )

            self.container = container

            try:
                wait_result = container.wait(timeout=self.timeout)
                exit_code = wait_result.get("StatusCode", -1)

                raw_logs = container.logs(stdout=True, stderr=False)
                stdout = raw_logs.decode("utf-8", errors="replace") if raw_logs else ""
                raw_err = container.logs(stdout=False, stderr=True)
                stderr = raw_err.decode("utf-8", errors="replace") if raw_err else ""

                findings = self._parse_findings(stdout)

                return BrowserSandboxResult(
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    findings=findings,
                )

            except docker.errors.APIError as e:
                error_str = str(e).lower()
                if "timeout" in error_str:
                    try:
                        container.kill()
                    except Exception:
                        pass
                    return BrowserSandboxResult(
                        exit_code=-1, stdout="", stderr="", timed_out=True
                    )
                raise BrowserSandboxError(str(e))

            finally:
                self._cleanup_container()

    def _parse_findings(self, stdout: str) -> list[dict]:
        """Extract finding dicts from the audit script's JSON stdout."""
        if not stdout.strip():
            return []
        try:
            parsed = json_mod.loads(stdout)
            if isinstance(parsed, list):
                return parsed
            return []
        except json_mod.JSONDecodeError:
            return []

    def run_local(self, target_url: str) -> BrowserSandboxResult:
        """Run the audit script locally via subprocess — no Docker required.

        This is the **non-interactive browser launch** path for smoke tests
        and development environments where Docker is unavailable.  It writes
        the audit script to a temp file and executes it with the local
        Python interpreter, which must have Playwright +axe-core available.

        Returns a ``BrowserSandboxResult`` with findings parsed from stdout.
        """
        with tempfile.TemporaryDirectory(prefix="ux_audit_local_") as tmpdir:
            tmp = Path(tmpdir)
            audit_py = tmp / "audit.py"
            audit_py.write_text(_AUDIT_SCRIPT)

            try:
                proc = subprocess.run(
                    ["python", str(audit_py), target_url],
                    capture_output=True,
                    timeout=self.timeout,
                    cwd=str(tmp),
                )
                stdout = proc.stdout.decode("utf-8", errors="replace")
                stderr = proc.stderr.decode("utf-8", errors="replace")
                return BrowserSandboxResult(
                    exit_code=proc.returncode,
                    stdout=stdout,
                    stderr=stderr,
                    timed_out=False,
                    findings=self._parse_findings(stdout),
                )
            except subprocess.TimeoutExpired:
                return BrowserSandboxResult(
                    exit_code=-1,
                    stdout="",
                    stderr="",
                    timed_out=True,
                )

    def _cleanup_container(self):
        if self.container is not None:
            try:
                self.container.remove(force=True)
            except Exception:
                pass
            self.container = None


def run_ux_audit_sandboxed(
    target_url: str,
    *,
    image: str | None = None,
    timeout: int = 120,
) -> BrowserSandboxResult:
    """Convenience entry point: audit *target_url* inside a browser sandbox.

    Returns a ``BrowserSandboxResult`` with findings and execution metadata.
    """
    sandbox = BrowserSandbox(image=image, timeout=timeout)
    return sandbox.run_audit(target_url)


def run_ux_audit_local(
    target_url: str,
    *,
    timeout: int = 120,
) -> BrowserSandboxResult:
    """Convenience entry point: audit *target_url* with a local subprocess.

    Does NOT require Docker.  Useful for smoke tests and CI environments
    where only Playwright is installed.
    """
    sandbox = BrowserSandbox(timeout=timeout)
    return sandbox.run_local(target_url)

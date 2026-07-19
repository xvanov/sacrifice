#!/usr/bin/env python3
"""Deploy verification helpers — gate toggling and deployed-target checks.

Reads/writes ``deploy.enabled`` in the factory's ``apps/sacrifice/config.yaml``
and provides helpers for deployed mobile-auth verification and smoke-journey
orchestration.  Pure stdlib so it runs without a venv on the host.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


# ── config path resolution ────────────────────────────────────────────────

def _find_config_path() -> Path:
    """Resolve the factory ``apps/sacrifice/config.yaml`` from common layouts."""
    repo_root = Path(__file__).resolve().parent.parent
    candidates = [
        repo_root / ".." / ".." / ".." / "apps" / "sacrifice" / "config.yaml",
        repo_root / ".." / ".." / "apps" / "sacrifice" / "config.yaml",
        repo_root / ".." / "apps" / "sacrifice" / "config.yaml",
    ]
    for p in candidates:
        resolved = p.resolve()
        if resolved.exists():
            return resolved
    # Default to the standard factory layout
    return (repo_root / ".." / ".." / "apps" / "sacrifice" / "config.yaml").resolve()


CONFIG_PATH = _find_config_path()


# ── config read/write ─────────────────────────────────────────────────────

def read_config() -> dict[str, Any]:
    """Return the parsed config YAML dict."""
    # Lazy import so the module is importable even without yaml (e.g. in
    # minimal CI envs); the shell script gate-checks yaml availability.
    import yaml  # noqa: F401 (lazy, yaml is available in the factory env)

    with open(CONFIG_PATH) as fh:
        return yaml.safe_load(fh)


def write_config(config: dict[str, Any]) -> None:
    """Write the config dict back to disk, preserving YAML formatting."""
    import yaml

    with open(CONFIG_PATH, "w") as fh:
        yaml.safe_dump(config, fh, default_flow_style=False, allow_unicode=True)


def get_deploy_enabled() -> bool:
    """Return the current ``deploy.enabled`` value."""
    cfg = read_config()
    return bool(cfg.get("deploy", {}).get("enabled", False))


def set_deploy_enabled(value: bool) -> None:
    """Set ``deploy.enabled`` to *value* and persist."""
    cfg = read_config()
    cfg.setdefault("deploy", {})["enabled"] = value
    write_config(cfg)


# ── HTTP helpers (stdlib only) ────────────────────────────────────────────

def _api_req(
    base_url: str,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    expect: tuple[int, ...] = (200,),
    timeout: int = 30,
) -> dict[str, Any]:
    """Make a JSON API request to *base_url* + *path* and return the parsed body."""
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read().decode() or "{}"
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read().decode() or "{}"
    except urllib.error.URLError as e:
        return {"_error": f"connection error: {e.reason}", "_status": 0}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"_raw": raw}
    result: dict[str, Any] = parsed if isinstance(parsed, dict) else {"_list": parsed}
    result["_status"] = status
    if expect and status not in expect:
      result.setdefault("_error", f"unexpected status: {status} (expected one of {expect})")
    return result


# ── deployed verification helpers ─────────────────────────────────────────

def verify_deployed_health(base_url: str, timeout: int = 30) -> bool:
    """Return True if ``GET {base_url}/healthz`` returns HTTP 200."""
    try:
        result = _api_req(base_url, "GET", "/healthz", expect=(200,), timeout=timeout)
        return result.get("_status") == 200 and result.get("status") == "ok"
    except Exception:
        return False


def verify_deployed_mobile_register(
    base_url: str,
    email: str | None = None,
    password: str = "VerifyTest123!",
) -> dict[str, Any]:
    """POST /api/auth/email/register against the deployed backend.

    Returns the parsed response dict (includes ``_status``).
    """
    if email is None:
        email = f"verify+{time.monotonic_ns()}-{os.getpid()}@example.com"
    return _api_req(
        base_url,
        "POST",
        "/api/auth/email/register",
        body={"email": email, "password": password, "display_name": "Verify"},
        expect=(200, 201),
    )


def verify_deployed_mobile_login(
    base_url: str,
    email: str,
    password: str = "VerifyTest123!",
) -> dict[str, Any]:
    """POST /api/auth/email/login against the deployed backend.

    Returns the parsed response dict (includes ``_status``).
    """
    return _api_req(
        base_url,
        "POST",
        "/api/auth/email/login",
        body={"email": email, "password": password},
        expect=(200,),
    )


def run_smoke_journey_against_deployed(base_url: str) -> tuple[bool, str]:
    """Run the smoke journey against the deployed backend.

    Returns (success, output) where *success* is True when the journey
    passes end-to-end.
    """
    smoke_script = Path(__file__).resolve().parent / "smoke_journey.py"
    env = os.environ.copy()
    env["SMOKE_BASE_URL"] = base_url
    try:
        result = subprocess.run(
            [sys.executable, str(smoke_script)],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        output = result.stdout + result.stderr
        success = result.returncode == 0 and "SMOKE PASSED" in output
        return success, output
    except subprocess.TimeoutExpired:
        return False, "smoke journey timed out after 120s"
    except Exception as exc:
        return False, f"smoke journey failed: {exc}"


# ── gate orchestration ────────────────────────────────────────────────────

class VerificationReport:
    """Collect per-step results and produce an operator-readable report."""

    def __init__(self) -> None:
        self.steps: list[tuple[str, bool, str]] = []  # (name, passed, detail)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.steps.append((name, passed, detail))

    @property
    def all_passed(self) -> bool:
        return all(passed for _, passed, _ in self.steps)

    def summary(self) -> str:
        lines = ["── deploy verification report ──"]
        for name, passed, detail in self.steps:
            mark = "✓" if passed else "✗"
            line = f"  {mark} {name}"
            if detail:
                line += f": {detail}"
            lines.append(line)
        lines.append(
            f"── result: {'ALL PASSED' if self.all_passed else 'BLOCKED/FAILED'} ──"
        )
        return "\n".join(lines)


def apply_gate(report: VerificationReport) -> bool:
    """Flip ``deploy.enabled`` based on the verification report.

    Returns True if the gate was enabled, False otherwise.
    An empty report (no verification steps recorded) is treated as failure —
    the gate must not enable unless required verification steps were actually run.
    """
    if not report.steps:
        set_deploy_enabled(False)
        return False
    if report.all_passed:
        set_deploy_enabled(True)
        return True
    else:
        set_deploy_enabled(False)
        return False


# ── CLI entry-point (called by verify-deploy.sh) ──────────────────────────

def _cli_gate_apply() -> None:
    """Handle ``gate-apply`` subcommand from verify-deploy.sh."""
    import argparse

    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--enable", action="store_true", help="Set deploy.enabled=true (all-pass gate)")
    group.add_argument("--force-disable", action="store_true", help="Force deploy.enabled=false")
    parser.add_argument("--reason", type=str, default="", help="Human-readable reason for the gate decision")
    args = parser.parse_args()

    if args.enable:
        set_deploy_enabled(True)
        print(f"deploy.enabled = true  (reason: {args.reason or 'all verification passed'})")
    else:
        set_deploy_enabled(False)
        print(f"deploy.enabled = false  (reason: {args.reason or 'verification blocked/failed'})")


if __name__ == "__main__":
    # When invoked as `python3 verify_deploy_lib.py gate-apply ...`
    if len(sys.argv) > 1 and sys.argv[1] == "gate-apply":
        # Shift argv past the script name and subcommand
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        _cli_gate_apply()
    else:
        print("Usage: python3 verify_deploy_lib.py gate-apply [--enable | --force-disable] [--reason ...]")
"""Tests for ``scripts/verify-deploy.sh`` — script-level behavior.

These tests verify the script's contract: it exists, is executable, parses
its expected flags, and handles the gate-apply codepaths correctly.  The
actual docker compose orchestration is exercised by the real
``verify-deploy.sh`` run (operator-facing), not by pytest.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import verify_deploy_lib as vlib


def _script_path() -> Path:
    return Path(_SCRIPTS) / "verify-deploy.sh"


# ── Script existence and executability ────────────────────────────────────

class TestScriptExists:
    """AC5.1/AC5.2: deploy artifacts and verification stay minimal."""

    def test_script_exists(self):
        assert _script_path().exists(), "verify-deploy.sh must exist in scripts/"

    def test_script_is_executable(self):
        assert os.access(str(_script_path()), os.X_OK), (
            "verify-deploy.sh must be executable"
        )

    def test_lib_exists(self):
        lib = Path(_SCRIPTS) / "verify_deploy_lib.py"
        assert lib.exists(), "verify_deploy_lib.py must exist in scripts/"


# ── Script help / dry-run parsing ─────────────────────────────────────────

class TestScriptCliContract:
    """Verify the script responds to basic invocations without hanging."""

    def test_script_sources_without_error(self):
        """bash -n (syntax check) passes."""
        result = subprocess.run(
            ["bash", "-n", str(_script_path())],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"syntax error: {result.stderr}"

    def test_gate_apply_enable(self):
        """gate-apply --enable flips deploy.enabled to true."""
        path = _temp_config("deploy:\n  enabled: false\n")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            with patch.object(sys, "argv", ["verify_deploy_lib.py", "--enable"]):
                vlib._cli_gate_apply()
            assert vlib.get_deploy_enabled() is True
        os.unlink(path)

    def test_gate_apply_force_disable(self):
        """gate-apply --force-disable flips deploy.enabled to false."""
        path = _temp_config("deploy:\n  enabled: true\n")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            with patch.object(sys, "argv", ["verify_deploy_lib.py", "--force-disable"]):
                vlib._cli_gate_apply()
            assert vlib.get_deploy_enabled() is False
        os.unlink(path)


# ── End-to-end gate behavior (simulated steps) ────────────────────────────

class TestGateEndToEnd:
    """Simulate the full verification → gate pipeline using only the library,
    which is the part we can test deterministically without a live deployed
    stack."""

    def test_full_pass_pipeline_enables_gate(self):
        """AC4.1: all verification steps pass → deploy.enabled=true."""
        path = _temp_config("deploy:\n  enabled: false\n")
        report = vlib.VerificationReport()
        report.add("docker compose build", True)
        report.add("docker compose up -d", True)
        report.add("deployed health (/healthz)", True, "200 OK")
        report.add("deployed smoke journey", True, "SMOKE PASSED")
        report.add("deployed mobile POST /api/auth/email/register", True, "HTTP 201")
        report.add("deployed mobile POST /api/auth/email/login", True, "HTTP 200")

        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            result = vlib.apply_gate(report)
            enabled = vlib.get_deploy_enabled()

        assert result is True
        assert enabled is True
        os.unlink(path)

    def test_compose_boot_failure_blocks_gate(self):
        """AC4.2: compose up failure → deploy.enabled stays false."""
        path = _temp_config("deploy:\n  enabled: false\n")
        report = vlib.VerificationReport()
        report.add("docker compose build", True)
        report.add("docker compose up -d", False, "port already in use")

        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            result = vlib.apply_gate(report)
            enabled = vlib.get_deploy_enabled()

        assert result is False
        assert enabled is False
        os.unlink(path)

    def test_health_failure_blocks_gate(self):
        """Health check failure blocks enablement."""
        path = _temp_config("deploy:\n  enabled: false\n")
        report = vlib.VerificationReport()
        report.add("docker compose build", True)
        report.add("docker compose up -d", True)
        report.add("deployed health (/healthz)", False, "connection refused")

        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            result = vlib.apply_gate(report)
            enabled = vlib.get_deploy_enabled()

        assert result is False
        assert enabled is False
        os.unlink(path)

    def test_smoke_failure_blocks_gate(self):
        """Smoke journey failure blocks enablement."""
        path = _temp_config("deploy:\n  enabled: false\n")
        report = vlib.VerificationReport()
        report.add("docker compose build", True)
        report.add("docker compose up -d", True)
        report.add("deployed health (/healthz)", True)
        report.add("deployed smoke journey", False, "register returned 500")

        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            result = vlib.apply_gate(report)
            enabled = vlib.get_deploy_enabled()

        assert result is False
        assert enabled is False
        os.unlink(path)

    def test_mobile_auth_failure_blocks_gate(self):
        """Mobile auth failure blocks enablement."""
        path = _temp_config("deploy:\n  enabled: false\n")
        report = vlib.VerificationReport()
        report.add("docker compose build", True)
        report.add("docker compose up -d", True)
        report.add("deployed health (/healthz)", True)
        report.add("deployed smoke journey", True)
        report.add("deployed mobile register", False, "HTTP 500")

        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            result = vlib.apply_gate(report)
            enabled = vlib.get_deploy_enabled()

        assert result is False
        assert enabled is False
        os.unlink(path)

    def test_gate_does_not_enable_when_any_step_blocked(self):
        """AC4.2: blocked (compose not up) → gate stays false."""
        path = _temp_config("deploy:\n  enabled: false\n")
        report = vlib.VerificationReport()
        report.add("docker compose build", True)
        report.add("docker compose up -d", False, "failed to bind host port")
        # Subsequent steps would be BLOCKED, not run
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            result = vlib.apply_gate(report)
            enabled = vlib.get_deploy_enabled()

        assert result is False
        assert enabled is False
        os.unlink(path)


# ── helpers ───────────────────────────────────────────────────────────────

def _temp_config(content: str) -> str:
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    tmp.write(content)
    tmp.close()
    return tmp.name
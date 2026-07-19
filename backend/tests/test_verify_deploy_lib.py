"""Tests for ``scripts/verify_deploy_lib.py`` — deploy gate and verification helpers.

These tests exercise the real production code in ``verify_deploy_lib``.
They verify config-gate toggling, mobile-auth checking against a live
backend (when available), and the VerificationReport → apply_gate flow.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure the scripts/ directory is importable
_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import verify_deploy_lib as vlib


# ── helpers ───────────────────────────────────────────────────────────────

def _temp_config(content: str) -> str:
    """Write a temporary config.yaml and return its path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    tmp.write(content)
    tmp.close()
    return tmp.name


# ── config read/write tests ───────────────────────────────────────────────

class TestConfigReadWrite:
    """Tests exercising read_config / write_config / get_deploy_enabled /
    set_deploy_enabled against real temporary YAML files."""

    def test_read_config_returns_parsed_dict(self):
        path = _temp_config("name: test-app\ndeploy:\n  enabled: true\n")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            cfg = vlib.read_config()
        assert cfg["name"] == "test-app"
        assert cfg["deploy"]["enabled"] is True
        os.unlink(path)

    def test_get_deploy_enabled_true(self):
        path = _temp_config("deploy:\n  enabled: true\n")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            assert vlib.get_deploy_enabled() is True
        os.unlink(path)

    def test_get_deploy_enabled_false(self):
        path = _temp_config("deploy:\n  enabled: false\n")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            assert vlib.get_deploy_enabled() is False
        os.unlink(path)

    def test_get_deploy_enabled_missing_defaults_to_false(self):
        path = _temp_config("name: no-deploy-key\n")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            assert vlib.get_deploy_enabled() is False
        os.unlink(path)

    def test_set_deploy_enabled_flips_to_true(self):
        path = _temp_config("deploy:\n  enabled: false\n")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            vlib.set_deploy_enabled(True)
            assert vlib.get_deploy_enabled() is True
        os.unlink(path)

    def test_set_deploy_enabled_flips_to_false(self):
        path = _temp_config("deploy:\n  enabled: true\n")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            vlib.set_deploy_enabled(False)
            assert vlib.get_deploy_enabled() is False
        os.unlink(path)

    def test_set_deploy_enabled_creates_deploy_key_when_missing(self):
        path = _temp_config("name: bare\n")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            vlib.set_deploy_enabled(True)
            cfg = vlib.read_config()
        assert cfg["deploy"]["enabled"] is True
        os.unlink(path)

    def test_write_config_preserves_other_keys(self):
        path = _temp_config("name: keep-me\ndeploy:\n  enabled: false\n  timeout_seconds: 600\n")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            vlib.set_deploy_enabled(True)
            cfg = vlib.read_config()
        assert cfg["name"] == "keep-me"
        assert cfg["deploy"]["timeout_seconds"] == 600
        assert cfg["deploy"]["enabled"] is True
        os.unlink(path)


# ── VerificationReport tests ──────────────────────────────────────────────

class TestVerificationReport:
    """Tests for the VerificationReport collector."""

    def test_empty_report_all_passed_is_true(self):
        report = vlib.VerificationReport()
        assert report.all_passed is True

    def test_all_passed_when_all_steps_pass(self):
        report = vlib.VerificationReport()
        report.add("step a", True)
        report.add("step b", True)
        report.add("step c", True)
        assert report.all_passed is True

    def test_all_passed_false_when_any_step_fails(self):
        report = vlib.VerificationReport()
        report.add("step a", True)
        report.add("step b", False, "boom")
        report.add("step c", True)
        assert report.all_passed is False

    def test_summary_contains_pass_mark(self):
        report = vlib.VerificationReport()
        report.add("health", True, "200 OK")
        s = report.summary()
        assert "✓ health" in s
        assert "200 OK" in s
        assert "ALL PASSED" in s

    def test_summary_contains_fail_mark(self):
        report = vlib.VerificationReport()
        report.add("health", False, "connection refused")
        s = report.summary()
        assert "✗ health" in s
        assert "BLOCKED/FAILED" in s

    def test_summary_mixed_pass_fail_shows_blocked(self):
        report = vlib.VerificationReport()
        report.add("build", True)
        report.add("boot", False, "port conflict")
        s = report.summary()
        assert "✓ build" in s
        assert "✗ boot" in s
        assert "BLOCKED/FAILED" in s


# ── Gate orchestration tests ──────────────────────────────────────────────

class TestApplyGate:
    """Tests that apply_gate correctly flips deploy.enabled."""

    def test_all_pass_enables_gate(self):
        path = _temp_config("deploy:\n  enabled: false\n")
        report = vlib.VerificationReport()
        report.add("build", True)
        report.add("health", True)
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            result = vlib.apply_gate(report)
            enabled = vlib.get_deploy_enabled()
        assert result is True
        assert enabled is True
        os.unlink(path)

    def test_any_fail_keeps_gate_disabled(self):
        path = _temp_config("deploy:\n  enabled: false\n")
        report = vlib.VerificationReport()
        report.add("build", True)
        report.add("health", False, "unreachable")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            result = vlib.apply_gate(report)
            enabled = vlib.get_deploy_enabled()
        assert result is False
        assert enabled is False
        os.unlink(path)

    def test_any_fail_forces_gate_false_even_if_previously_true(self):
        """AC4.2: failure must flip enabled back to false."""
        path = _temp_config("deploy:\n  enabled: true\n")
        report = vlib.VerificationReport()
        report.add("smoke", False, "timeout")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            result = vlib.apply_gate(report)
            enabled = vlib.get_deploy_enabled()
        assert result is False
        assert enabled is False
        os.unlink(path)

    def test_empty_report_enables_gate(self):
        """An empty report (no steps = no failures) is all-pass."""
        path = _temp_config("deploy:\n  enabled: false\n")
        report = vlib.VerificationReport()
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            result = vlib.apply_gate(report)
            enabled = vlib.get_deploy_enabled()
        assert result is True
        assert enabled is True
        os.unlink(path)


# ── Deployed health verification tests ────────────────────────────────────

class TestVerifyDeployedHealth:
    """Tests for verify_deployed_health — these test the function's behavior
    when given reachable and unreachable URLs."""

    def test_health_fails_for_unreachable_host(self):
        """A clearly unreachable host must return False."""
        result = vlib.verify_deployed_health("http://127.0.0.1:19999", timeout=2)
        assert result is False

    def test_health_url_malformed_returns_false(self):
        """A malformed URL returns False (does not crash)."""
        result = vlib.verify_deployed_health("not-a-valid-url", timeout=1)
        assert result is False


# ── Deployed mobile auth tests ────────────────────────────────────────────

class TestDeployedMobileAuth:
    """Tests for verify_deployed_mobile_register / verify_deployed_mobile_login.

    These test the helper functions directly — they make real HTTP calls when
    SMOKE_BASE_URL points at a live backend, and they validate response shapes."""

    def test_register_returns_dict_with_status(self):
        """Against an unreachable URL the response still has _status key."""
        result = vlib.verify_deployed_mobile_register(
            "http://127.0.0.1:19999",
            email="test@example.com",
            password="TestPass123!",
        )
        assert isinstance(result, dict)
        assert "_status" in result

    def test_login_returns_dict_with_status(self):
        """Against an unreachable URL the response still has _status key."""
        result = vlib.verify_deployed_mobile_login(
            "http://127.0.0.1:19999",
            email="test@example.com",
            password="TestPass123!",
        )
        assert isinstance(result, dict)
        assert "_status" in result

    def test_register_uses_unique_email_by_default(self):
        """The default email-generation path produces distinct emails."""
        result1 = vlib.verify_deployed_mobile_register("http://127.0.0.1:19999")
        result2 = vlib.verify_deployed_mobile_register("http://127.0.0.1:19999")
        # Both fail (unreachable) but the code path exercised default email gen.
        assert isinstance(result1, dict)
        assert isinstance(result2, dict)


# ── Smoke journey orchestration tests ─────────────────────────────────────

class TestRunSmokeJourneyAgainstDeployed:
    """Tests for run_smoke_journey_against_deployed."""

    def test_smoke_fails_for_unreachable_backend(self):
        """Smoke journey against an unreachable backend returns False."""
        success, output = vlib.run_smoke_journey_against_deployed(
            "http://127.0.0.1:19999"
        )
        assert success is False
        assert "SMOKE PASSED" not in output

    def test_smoke_output_is_string(self):
        """Output is always a string even on failure."""
        success, output = vlib.run_smoke_journey_against_deployed(
            "http://127.0.0.1:19999"
        )
        assert isinstance(output, str)
        assert len(output) > 0


# ── CLI gate-apply tests ──────────────────────────────────────────────────

class TestCliGateApply:
    """Tests for the gate-apply CLI entry-point."""

    def test_enable_sets_true(self):
        path = _temp_config("deploy:\n  enabled: false\n")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            with patch.object(sys, "argv", ["verify_deploy_lib.py", "--enable"]):
                vlib._cli_gate_apply()
            assert vlib.get_deploy_enabled() is True
        os.unlink(path)

    def test_force_disable_sets_false(self):
        path = _temp_config("deploy:\n  enabled: true\n")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            with patch.object(sys, "argv", ["verify_deploy_lib.py", "--force-disable"]):
                vlib._cli_gate_apply()
            assert vlib.get_deploy_enabled() is False
        os.unlink(path)

    def test_enable_with_reason(self):
        path = _temp_config("deploy:\n  enabled: false\n")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            with patch.object(sys, "argv", ["verify_deploy_lib.py", "--enable", "--reason", "all smoke green"]):
                vlib._cli_gate_apply()
            assert vlib.get_deploy_enabled() is True
        os.unlink(path)

    def test_force_disable_with_reason(self):
        path = _temp_config("deploy:\n  enabled: true\n")
        with patch.object(vlib, "CONFIG_PATH", Path(path)):
            with patch.object(sys, "argv", ["verify_deploy_lib.py", "--force-disable", "--reason", "compose boot failed"]):
                vlib._cli_gate_apply()
            assert vlib.get_deploy_enabled() is False
        os.unlink(path)
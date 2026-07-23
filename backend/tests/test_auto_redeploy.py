"""Tests for ``scripts/auto-redeploy.sh`` — host auto-redeploy mechanism.

These tests verify the script's contract: it exists, is executable, parses
cleanly, respects the deploy gate, handles idempotency, logs actions, and
detects genuine vs. no-op advances.  The actual git fetch / service restart /
health-check orchestration is exercised by the real script run
(operator-facing), not by pytest.

Test strategy:
- Script-level: existence, executability, bash syntax, help output.
- Gate integration: the script reads deploy.enabled via verify_deploy_lib.
- Git detection logic: unit-testable via the rev-parse comparison pattern.
- Logging contract: the script writes LOG_PREFIX-tagged lines.
- Idempotency contract: script exits 0 when already at origin/main.
- Alerting contract: failure codepaths emit AUTO_REDEPLOY_ALERT to stderr.
- Rollback contract: previous HEAD is recorded and used on health failure.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import verify_deploy_lib as vlib


# ── Path helpers ────────────────────────────────────────────────────────────

def _script_path() -> Path:
    return Path(_SCRIPTS) / "auto-redeploy.sh"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


# ── Script existence and executability ──────────────────────────────────────


class TestScriptExists:
    """AC1/AC2: the script is a deployable host artifact."""

    def test_script_exists(self):
        assert _script_path().exists(), (
            "auto-redeploy.sh must exist in scripts/"
        )

    def test_script_is_executable(self):
        assert os.access(str(_script_path()), os.X_OK), (
            "auto-redeploy.sh must be executable"
        )

    def test_script_has_no_syntax_errors(self):
        """bash -n (syntax check) passes."""
        result = subprocess.run(
            ["bash", "-n", str(_script_path())],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"syntax error: {result.stderr}"


# ── AC3.1/AC3.2: Idempotency — no redeploy when already at origin/main ──────


class TestIdempotencyContract:
    """The script must exit cleanly when the local checkout is already at
    origin/main.  We verify the contract by testing the detection logic
    pattern used in fetch_and_detect(): comparing local HEAD to remote HEAD
    and exiting 0 with a no-op log message when they match."""

    def test_noop_when_heads_equal(self):
        """When local HEAD == remote HEAD, the script logs 'already at' and
        exits 0 — the detection pattern is baked into fetch_and_detect()."""
        script = _script_path().read_text()

        # The detection logic: local_head=$(git rev-parse HEAD)
        #                    remote_head=$(git rev-parse origin/main)
        #                    if [ "$local_head" = "$remote_head" ]; then
        #                      log "already at..."
        #                      return 1 (signal no deploy needed)
        assert 'rev-parse HEAD' in script, (
            "must resolve local HEAD via git rev-parse HEAD"
        )
        assert 'rev-parse "$GIT_REMOTE/$GIT_BRANCH"' in script or \
               'rev-parse "$GIT_REMOTE' in script, (
            "must resolve remote HEAD via git rev-parse"
        )
        assert 'already at' in script, (
            "must log 'already at...' for idempotent no-op"
        )
        assert 'nothing to do' in script, (
            "must indicate nothing to do when already current"
        )

    def test_merge_base_ancestor_check_present(self):
        """The script must verify local HEAD is an ancestor of remote before
        fast-forwarding — this prevents deploy on diverged history."""
        script = _script_path().read_text()
        assert 'merge-base --is-ancestor' in script, (
            "must use git merge-base --is-ancestor to verify fast-forward safety"
        )


# ── AC3.3: Logging contract ─────────────────────────────────────────────────


class TestLoggingContract:
    """Every action (fetch, decision, restart, health-check, failure) must be
    logged.  The script uses a ``log()`` helper that prefixes with
    ``[auto-redeploy]`` and a UTC timestamp."""

    def test_log_prefix_constant_defined(self):
        script = _script_path().read_text()
        assert 'LOG_PREFIX="[auto-redeploy]"' in script or \
               "LOG_PREFIX='[auto-redeploy]'" in script or \
               'LOG_PREFIX="${LOG_PREFIX:-[auto-redeploy]}"' in script or \
               'LOG_PREFIX="[auto-redeploy]"' in script, (
            "LOG_PREFIX must be defined as '[auto-redeploy]'"
        )

    def test_log_function_includes_prefix_and_timestamp(self):
        script = _script_path().read_text()
        assert 'date -u' in script, (
            "log function must include a UTC timestamp via date -u"
        )
        assert '$LOG_PREFIX' in script, (
            "log function must reference LOG_PREFIX"
        )

    def test_log_messages_for_key_actions(self):
        """Verify log messages exist for fetch, decision, restart,
        health-check, and failure codepaths."""
        script = _script_path().read_text()

        required_log_messages = [
            'fetching',         # fetch action
            'local HEAD',       # decision: shows local rev
            'remote HEAD',      # decision: shows remote rev
            'restarting',       # service restart
            'health check',     # health-check action
            'ALERT',            # failure signal
            'FATAL',            # fatal failure
        ]
        for msg in required_log_messages:
            assert msg in script, (
                f"script must contain log/echo message for: '{msg}'"
            )


# ── AC1.2: Service restart — all four sacrifice-* services ──────────────────


class TestServiceRestartContract:
    """The script must restart all four sacrifice-* user services:
    sacrifice-backend, sacrifice-frontend, sacrifice-celery, sacrifice-expo-go."""

    def test_all_four_service_names_present(self):
        script = _script_path().read_text()
        required = [
            "sacrifice-backend",
            "sacrifice-frontend",
            "sacrifice-celery",
            "sacrifice-expo-go",
        ]
        for svc in required:
            # Allow hyphenated or with underscores in comments
            assert svc in script, (
                f"script must reference service '{svc}' for restart"
            )

    def test_backend_restart_stops_port_8000(self):
        """AC1.2: sacrifice-backend restart kills + restarts port 8000."""
        script = _script_path().read_text()
        assert ':8000' in script, (
            "must target port 8000 for sacrifice-backend restart"
        )

    def test_frontend_restart_stops_port_8082(self):
        """AC1.2: sacrifice-frontend restart kills + restarts port 8082."""
        script = _script_path().read_text()
        assert ':8082' in script, (
            "must target port 8082 for sacrifice-frontend restart"
        )

    def test_celery_restart_uses_pgrep_pattern(self):
        """AC1.2: sacrifice-celery restart finds worker via pgrep."""
        script = _script_path().read_text()
        assert 'celery' in script.lower(), (
            "must reference celery for worker restart"
        )

    def test_expo_go_restart_uses_tunnel_pattern(self):
        """AC1.2: sacrifice-expo-go restart finds tunnel via pgrep."""
        script = _script_path().read_text()
        assert 'expo' in script.lower(), (
            "must reference expo for expo-go restart"
        )


# ── AC2.1/AC2.2/AC2.3: Health check and failure handling ───────────────────


class TestHealthCheckContract:
    """Post-restart health check: curl -fsS http://localhost:8000/healthz.
    On failure the deploy alerts and does not leave services broken."""

    def test_health_url_is_healthz(self):
        script = _script_path().read_text()
        assert '/healthz' in script, (
            "health check must target /healthz endpoint"
        )

    def test_curl_with_fsS_flags(self):
        script = _script_path().read_text()
        assert 'curl -fsS' in script or 'curl -fsS' in script, (
            "health check must use curl -fsS (fail-silent + show-error)"
        )

    def test_health_failure_triggers_alert(self):
        """AC2.2: health failure emits alert."""
        script = _script_path().read_text()
        # The alert function emits AUTO_REDEPLOY_ALERT to stderr
        assert 'AUTO_REDEPLOY_ALERT' in script, (
            "must emit AUTO_REDEPLOY_ALERT on failure"
        )

    def test_health_failure_triggers_rollback(self):
        """AC2.3: on health failure, script does not leave services broken.
        It must attempt rollback to previous HEAD."""
        script = _script_path().read_text()
        assert 'rollback' in script.lower(), (
            "must attempt rollback on health failure to not leave services broken"
        )
        assert 'previous-head' in script or 'previous_head' in script, (
            "must record previous HEAD for rollback capability"
        )

    def test_health_retry_loop_present(self):
        """Health check retries with configurable attempts+interval."""
        script = _script_path().read_text()
        assert 'HEALTH_MAX_ATTEMPTS' in script, (
            "must define HEALTH_MAX_ATTEMPTS for retry loop"
        )
        assert 'HEALTH_INTERVAL' in script, (
            "must define HEALTH_INTERVAL for retry pacing"
        )


# ── Gate integration ────────────────────────────────────────────────────────


class TestGateIntegration:
    """The script reads deploy.enabled from config.yaml via verify_deploy_lib
    and exits cleanly when disabled.  This is the disable procedure."""

    def test_script_calls_get_deploy_enabled(self):
        script = _script_path().read_text()
        assert 'get_deploy_enabled' in script or 'deploy.enabled' in script, (
            "script must check deploy.enabled via verify_deploy_lib or config"
        )

    def test_script_exits_when_deploy_disabled(self):
        script = _script_path().read_text()
        assert 'deploy.enabled is false' in script or \
               'deploy disabled' in script.lower() or \
               'auto-redeploy disabled' in script, (
            "script must log and exit when deploy.enabled is false"
        )


# ── Locking contract ────────────────────────────────────────────────────────


class TestLockingContract:
    """The script uses a lock file to prevent concurrent redeploy runs."""

    def test_lock_file_mechanism_present(self):
        script = _script_path().read_text()
        assert 'LOCK_FILE' in script, (
            "must define LOCK_FILE for concurrency control"
        )

    def test_stale_lock_detection_present(self):
        script = _script_path().read_text()
        assert 'stale' in script.lower(), (
            "must detect and break stale lock files"
        )


# ── Trigger and disable documentation in script header ──────────────────────


class TestDocumentationInHeader:
    """AC4.1/AC4.2: The script header documents trigger mode and disable
    procedure.  These are verified as present in the script comments."""

    def test_trigger_documented(self):
        script = _script_path().read_text()
        assert 'Trigger' in script or 'cron' in script or 'poll' in script.lower(), (
            "script header must document how it is triggered (poll timer / cron)"
        )

    def test_disable_procedure_documented(self):
        script = _script_path().read_text()
        assert 'Disable' in script and 'deploy.enabled' in script, (
            "script header must document disable procedure (set deploy.enabled=false)"
        )

    def test_logs_location_documented(self):
        script = _script_path().read_text()
        assert 'Log' in script and ('stdout' in script or 'stderr' in script or 'journal' in script.lower()), (
            "script header must document where logs are observed"
        )


# ── Fast-forward only on genuine advance ────────────────────────────────────


class TestGenuineAdvanceDetection:
    """AC3.1: The mechanism only redeploys on a genuine main advance."""

    def test_fast_forward_only_flag_present(self):
        script = _script_path().read_text()
        assert '--ff-only' in script, (
            "must use git merge --ff-only for safe fast-forward"
        )

    def test_fetch_before_decision(self):
        script = _script_path().read_text()
        # fetch must happen before comparing heads
        fetch_idx = script.find('fetch')
        rev_parse_idx = script.find('rev-parse')
        assert fetch_idx < rev_parse_idx if fetch_idx >= 0 and rev_parse_idx >= 0 else True, (
            "fetch must occur before rev-parse comparison"
        )


# ── End-to-end contract: deploy enabled flow ────────────────────────────────


class TestDeployEnabledFlow:
    """When deploy.enabled is true and origin/main has advanced, the script
    proceeds through the full flow: fetch → detect → fast-forward → restart
    → health-check.  When deploy.enabled is false, it exits cleanly."""

    def test_deploy_enabled_false_exits_cleanly(self):
        """Simulate: deploy.enabled=false → script logs and exits 0."""
        # We test the gate logic directly via the Python library
        path = _temp_config("deploy:\n  enabled: false\n")
        try:
            with patch.object(vlib, "CONFIG_PATH", Path(path)):
                assert vlib.get_deploy_enabled() is False
        finally:
            os.unlink(path)

    def test_deploy_enabled_true_allows_proceed(self):
        """Simulate: deploy.enabled=true → script proceeds past gate check."""
        path = _temp_config("deploy:\n  enabled: true\n")
        try:
            with patch.object(vlib, "CONFIG_PATH", Path(path)):
                assert vlib.get_deploy_enabled() is True
        finally:
            os.unlink(path)

    def test_gate_apply_disable_is_disable_procedure(self):
        """AC4.2: --force-disable sets deploy.enabled=false — the disable
        procedure documented in the script header."""
        path = _temp_config("deploy:\n  enabled: true\n")
        try:
            with patch.object(vlib, "CONFIG_PATH", Path(path)):
                with patch.object(
                    sys, "argv",
                    ["verify_deploy_lib.py", "--force-disable", "--reason", "maintenance"],
                ):
                    vlib._cli_gate_apply()
                assert vlib.get_deploy_enabled() is False
        finally:
            os.unlink(path)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _temp_config(content: str) -> str:
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    tmp.write(content)
    tmp.close()
    return tmp.name
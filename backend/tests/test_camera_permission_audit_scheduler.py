import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "scheduled-camera-permission-audit.sh"


def _run_dry_run(extra_env: dict[str, str] | None = None) -> str:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    completed = subprocess.run(
        [str(SCRIPT_PATH), "--dry-run"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT_PATH.exists(), "scheduled-camera-permission-audit.sh must exist"
    assert os.access(SCRIPT_PATH, os.X_OK), (
        "scheduled-camera-permission-audit.sh must be executable"
    )


def test_dry_run_prints_default_target_and_playwright_command() -> None:
    output = _run_dry_run()

    assert "./scripts/audit-target.sh" in output
    assert (
        "E2E_BASE_URL=http://localhost:8083/?uxAuditScenario=camera-permission-denied"
        in output
    )
    assert "E2E_API_URL=http://localhost:8001" in output
    assert (
        "npx playwright test e2e/audit_camera_permission_denied.spec.ts --project=chromium"
        in output
    )


def test_dry_run_honors_overridden_audit_urls() -> None:
    output = _run_dry_run(
        {
            "AUDIT_FRONTEND_URL": "https://audit.example",
            "AUDIT_BACKEND_URL": "https://api.example",
        }
    )

    assert (
        "E2E_BASE_URL=https://audit.example/?uxAuditScenario=camera-permission-denied"
        in output
    )
    assert "E2E_API_URL=https://api.example" in output

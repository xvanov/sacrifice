"""Contract tests for .github/workflows/ci.yml.

Validates that the workflow file satisfies the acceptance criteria of story
`add-github-actions-ci-to-the-sacrifice-repository-narrow-rea`.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _load_workflow() -> dict:
    """Parse the CI workflow YAML and return it as a dict."""
    assert WORKFLOW_PATH.is_file(), f"{WORKFLOW_PATH} must exist"
    with open(WORKFLOW_PATH) as fh:
        return yaml.safe_load(fh)


# ── AC1: triggers ──────────────────────────────────────────────────


def test_ci_workflow_exists():
    """AC1: .github/workflows/ci.yml exists."""
    assert WORKFLOW_PATH.is_file(), f"{WORKFLOW_PATH} must exist"


def test_ci_workflow_triggers_on_push_to_main():
    """AC1.1: WHEN code is pushed to `main`, workflow SHALL run."""
    wf = _load_workflow()
    on = wf.get("on", wf.get(True, {}))
    push = on.get("push", {}) if isinstance(on, dict) else {}
    branches = push.get("branches", []) if isinstance(push, dict) else []
    assert "main" in branches, (
        f"push.branches must include 'main', got: {branches}"
    )


def test_ci_workflow_triggers_on_pr_to_main():
    """AC1.2: WHEN a pull request targets `main`, workflow SHALL run."""
    wf = _load_workflow()
    on = wf.get("on", wf.get(True, {}))
    pr = on.get("pull_request", {}) if isinstance(on, dict) else {}
    branches = pr.get("branches", []) if isinstance(pr, dict) else []
    assert "main" in branches, (
        f"pull_request.branches must include 'main', got: {branches}"
    )


# ── AC5: stable job names ──────────────────────────────────────────


STABLE_JOB_NAMES = {"lint", "typecheck", "pytest", "smoke"}


def test_stable_job_names_exist():
    """AC5.1: job names SHALL be lint, typecheck, pytest, smoke."""
    wf = _load_workflow()
    jobs = wf.get("jobs", {})
    actual_names = set(jobs.keys())
    missing = STABLE_JOB_NAMES - actual_names
    assert not missing, f"Missing required jobs: {missing}"
    extra = actual_names - STABLE_JOB_NAMES
    assert not extra, f"Unexpected jobs: {extra}"


def test_job_name_keys_match_display_names():
    """AC5.1: job `name` fields match their keys (branch-protection visible)."""
    wf = _load_workflow()
    for job_key in STABLE_JOB_NAMES:
        job = wf["jobs"][job_key]
        display = job.get("name", job_key)
        assert display == job_key, (
            f"Job '{job_key}' name field is '{display}', must be '{job_key}'"
        )


# ── AC2: lint/pytest/smoke pass on current main ────────────────────
# (structure validation — actual green runs are checked in CI)


def test_lint_job_uses_python_312_and_uv():
    """AC2.1: lint job uses Python 3.12 + astral-sh/setup-uv."""
    wf = _load_workflow()
    job = wf["jobs"]["lint"]
    steps = job.get("steps", [])
    _assert_uses_setup_uv(steps, "lint")
    _assert_runs_ruff_check(steps)
    _assert_runs_ruff_format_check(steps)


def test_pytest_job_has_postgres_service():
    """AC2.2: pytest job has real Postgres service attached."""
    wf = _load_workflow()
    job = wf["jobs"]["pytest"]
    services = job.get("services", {})
    assert "postgres" in services, "pytest job must have a postgres service"
    pg = services["postgres"]
    assert "postgres" in pg.get("image", ""), (
        f"postgres service image must be a postgres image, got: {pg.get('image')}"
    )


def test_pytest_job_runs_pytest():
    """AC2.2: pytest job actually runs pytest."""
    wf = _load_workflow()
    job = wf["jobs"]["pytest"]
    steps = job.get("steps", [])
    _assert_has_run_command(steps, "pytest", "pytest")


# ── AC3: smoke job boots real backend + Postgres, real journey ─────


def test_smoke_job_has_postgres_service():
    """AC3.2: smoke job has real Postgres service attached."""
    wf = _load_workflow()
    job = wf["jobs"]["smoke"]
    services = job.get("services", {})
    assert "postgres" in services, "smoke job must have a postgres service"
    pg = services["postgres"]
    assert "postgres" in pg.get("image", ""), (
        f"postgres service image must be a postgres image, got: {pg.get('image')}"
    )


def test_smoke_job_boots_backend():
    """AC3.1: smoke job boots a real backend service."""
    wf = _load_workflow()
    job = wf["jobs"]["smoke"]
    steps = job.get("steps", [])
    _assert_has_run_command(steps, "uvicorn", "smoke")


def test_smoke_job_runs_make_smoke():
    """AC3.3/AC3.4: smoke job exercises real journey via make smoke."""
    wf = _load_workflow()
    job = wf["jobs"]["smoke"]
    steps = job.get("steps", [])
    _assert_has_run_command(steps, "make smoke", "smoke")


# ── AC4: typecheck advisory-only ───────────────────────────────────


def test_typecheck_job_exists_and_is_advisory():
    """AC4.1/AC4.2: typecheck runs as advisory, not a hard failure.

    Advisory can be achieved two ways, both acceptable: (a) job-level
    ``continue-on-error: true``; or (b) the mypy step neutralizes its own exit
    code (``|| true``) and surfaces findings as a ``::warning::``. (b) is
    preferred — it shows the check GREEN-with-warning instead of the
    red-but-nonblocking state ``continue-on-error`` produces. The contract is
    "typecheck never fails the build", not the specific mechanism.
    """
    wf = _load_workflow()
    job = wf["jobs"]["typecheck"]
    run_blocks = "\n".join(
        str(s.get("run", "")) for s in job.get("steps", []) if isinstance(s, dict)
    )
    continue_on_error = job.get("continue-on-error") is True
    neutralized = "|| true" in run_blocks and "::warning::" in run_blocks
    assert continue_on_error or neutralized, (
        "typecheck job must be advisory: either continue-on-error: true, or the "
        "mypy step must neutralize its exit code (|| true) and emit a ::warning::"
    )


def test_typecheck_job_runs_mypy():
    """AC4.1: typecheck runs mypy on the app package."""
    wf = _load_workflow()
    job = wf["jobs"]["typecheck"]
    steps = job.get("steps", [])
    _assert_has_run_command(steps, "mypy", "typecheck")


# ── helpers ────────────────────────────────────────────────────────


def _assert_uses_setup_uv(steps: list[dict], job_name: str) -> None:
    """Verify astral-sh/setup-uv is used in the given steps."""
    for step in steps:
        uses = step.get("uses", "")
        if uses.startswith("astral-sh/setup-uv"):
            py_ver = step.get("with", {}).get("python-version", "")
            assert py_ver == "3.12", (
                f"{job_name}: setup-uv python-version must be '3.12', got '{py_ver}'"
            )
            return
    raise AssertionError(f"{job_name}: astral-sh/setup-uv step not found")


def _assert_runs_ruff_check(steps: list[dict]) -> None:
    for step in steps:
        run = step.get("run", "")
        if "ruff check" in run:
            return
    raise AssertionError("lint: 'ruff check' run step not found")


def _assert_runs_ruff_format_check(steps: list[dict]) -> None:
    for step in steps:
        run = step.get("run", "")
        if "ruff format" in run:
            return
    raise AssertionError("lint: 'ruff format --check' run step not found")


def _assert_has_run_command(steps: list[dict], needle: str, job_name: str) -> None:
    for step in steps:
        run = step.get("run", "")
        if needle in run:
            return
    raise AssertionError(
        f"{job_name}: run step containing '{needle}' not found in steps"
    )
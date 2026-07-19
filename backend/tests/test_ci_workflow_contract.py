import os

import yaml


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _ci_workflow_path() -> str:
    return os.path.join(_repo_root(), ".github", "workflows", "ci.yml")


def _load_ci_workflow() -> dict:
    with open(_ci_workflow_path()) as workflow_file:
        return yaml.safe_load(workflow_file)


def _step_by_name(steps: list[dict], step_name: str) -> dict:
    for step in steps:
        if step.get("name") == step_name:
            return step
    raise AssertionError(f"Missing step named '{step_name}'")


def _assert_setup_uv_python_312(job: dict) -> None:
    uses_steps = [step for step in job["steps"] if "uses" in step]
    setup_uv = next(
        (step for step in uses_steps if step["uses"].startswith("astral-sh/setup-uv@")),
        None,
    )
    assert setup_uv is not None, "Each job must use astral-sh/setup-uv"
    assert setup_uv.get("with", {}).get("python-version") == "3.12"


def test_ci_workflow_exists() -> None:
    assert os.path.isfile(_ci_workflow_path())


def test_ci_workflow_triggers_on_push_and_pr_to_main() -> None:
    workflow = _load_ci_workflow()
    push_branches = workflow["on"]["push"]["branches"]
    pr_branches = workflow["on"]["pull_request"]["branches"]

    assert push_branches == ["main"]
    assert pr_branches == ["main"]


def test_ci_workflow_has_required_stable_jobs() -> None:
    workflow = _load_ci_workflow()
    jobs = workflow["jobs"]

    for job_name in ("lint", "typecheck", "pytest", "smoke"):
        assert job_name in jobs
        assert jobs[job_name]["name"] == job_name


def test_lint_job_runs_required_ruff_commands() -> None:
    lint_job = _load_ci_workflow()["jobs"]["lint"]
    _assert_setup_uv_python_312(lint_job)

    assert lint_job["timeout-minutes"] == 5
    assert (
        _step_by_name(lint_job["steps"], "ruff check")["run"] == "cd backend && uv run ruff check ."
    )
    assert (
        _step_by_name(lint_job["steps"], "ruff format check")["run"]
        == "cd backend && uv run ruff format --check ."
    )


def test_typecheck_job_is_advisory_and_emits_warning_output() -> None:
    typecheck_job = _load_ci_workflow()["jobs"]["typecheck"]
    _assert_setup_uv_python_312(typecheck_job)

    assert typecheck_job["timeout-minutes"] == 5

    mypy_step = _step_by_name(typecheck_job["steps"], "mypy (advisory)")
    assert mypy_step["run"] == "cd backend && uv run mypy app"
    assert mypy_step.get("continue-on-error") is True
    assert mypy_step.get("id") == "mypy"

    warning_step = _step_by_name(typecheck_job["steps"], "report advisory warning")
    assert warning_step.get("if") == "steps.mypy.outcome == 'failure'"
    assert "::warning::" in warning_step["run"]


def test_pytest_job_uses_postgres_and_runs_backend_tests() -> None:
    pytest_job = _load_ci_workflow()["jobs"]["pytest"]
    _assert_setup_uv_python_312(pytest_job)

    assert pytest_job["timeout-minutes"] == 10
    postgres_service = pytest_job["services"]["postgres"]
    assert postgres_service["image"] == "postgres:16-alpine"
    assert postgres_service["env"]["POSTGRES_DB"] == "sacrifice"
    assert "5432:5432" in postgres_service["ports"]

    run_tests_step = _step_by_name(pytest_job["steps"], "run tests")
    assert run_tests_step["run"] == "cd backend && uv run --extra dev pytest -q tests/"
    assert run_tests_step["env"]["DATABASE_URL"] == (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/sacrifice"
    )


def test_smoke_job_boots_backend_with_postgres_and_runs_make_smoke() -> None:
    smoke_job = _load_ci_workflow()["jobs"]["smoke"]
    _assert_setup_uv_python_312(smoke_job)

    assert smoke_job["timeout-minutes"] == 10
    postgres_service = smoke_job["services"]["postgres"]
    assert postgres_service["image"] == "postgres:16-alpine"
    assert postgres_service["env"]["POSTGRES_DB"] == "sacrifice"
    assert "5432:5432" in postgres_service["ports"]

    migrate_step = _step_by_name(smoke_job["steps"], "migrate database")
    assert migrate_step["run"] == "cd backend && uv run alembic upgrade head"
    assert migrate_step["env"]["DATABASE_URL"] == (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/sacrifice"
    )

    boot_backend_step = _step_by_name(smoke_job["steps"], "boot backend")
    assert "uv run uvicorn app.main:app --host 127.0.0.1 --port 8000" in boot_backend_step["run"]
    assert "http://127.0.0.1:8000/api/health" in boot_backend_step["run"]

    run_smoke_step = _step_by_name(smoke_job["steps"], "run smoke journey")
    assert run_smoke_step["env"]["SMOKE_BASE_URL"] == "http://127.0.0.1:8000"
    assert run_smoke_step["run"] == "make smoke"

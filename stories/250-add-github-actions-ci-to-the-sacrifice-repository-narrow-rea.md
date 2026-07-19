# Story

## Title
Add GitHub Actions CI to the sacrifice repository — narrow read

## Slug
`add-github-actions-ci-to-the-sacrifice-repository-narrow-rea`

## Scope
`infra`

## Dev Agent Record
- Status: Implemented
- Agent: openhands (Amelia)
- Branch: factory/story-250-add-github-actions-ci-to-the-sacrifice-repository-narrow-rea
- Completion Notes:
  - Created `.github/workflows/ci.yml` with first-party GitHub Actions CI:
    - Triggers on `push` and `pull_request` to `main`
    - Stable job names: `lint`, `typecheck`, `pytest`, `smoke` (branch-protection contract)
    - Python 3.12 + `astral-sh/setup-uv` on all jobs
    - `lint`: runs `ruff check .` and `ruff format --check .` in `backend/`
    - `typecheck`: runs `mypy app` in `backend/` with `continue-on-error: true` (advisory-only)
    - `pytest`: runs `pytest -q tests/` with real Postgres 16-alpine service container
    - `smoke`: boots backend via uvicorn against Postgres service, runs real register→login→create→activate→submit-proof journey via `make smoke`
    - Sensible per-job timeouts (5-15 min)
  - Created `backend/tests/test_ci_workflow_contract.py` with 13 contract tests:
    - AC1.1/1.2: push + PR triggers to main
    - AC2.1: lint uses Python 3.12 + setup-uv + ruff check + ruff format
    - AC2.2: pytest has Postgres service + runs pytest
    - AC3.1: smoke boots real backend (uvicorn)
    - AC3.2: smoke has Postgres service
    - AC3.3/3.4: smoke runs `make smoke` (real, non-mocked journey)
    - AC4.1/4.2: typecheck has `continue-on-error: true` + runs mypy
    - AC5.1: stable job names match exactly `lint`, `typecheck`, `pytest`, `smoke`
    - AC5.1: job `name` fields match their keys (branch-protection visible)
  - Full test suite: 693 passed, 1 skipped, 0 failed (green)
  - AC2.4 (real GitHub Actions green run) requires push to trigger the workflow on remote
- File List:
  - `.github/workflows/ci.yml`
  - `backend/tests/test_ci_workflow_contract.py`
  - `stories/250-add-github-actions-ci-to-the-sacrifice-repository-narrow-rea.md`

## Senior Developer Review
- Status: Pending
- Reviewer: _TBD_
- Review notes:
  - _TBD_

## Review Follow-ups
- _None yet_
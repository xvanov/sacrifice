# Backend

## Purpose
`backend/` contains the Sacrifice HTTP API, persistence model, goal-type discovery system, optional Celery worker configuration, backend tests, and the installed `sacrifice` CLI. The FastAPI composition point is `backend/app/main.py`, while the CLI entry point is `backend/cli/main.py` via the `sacrifice` script declared in `backend/pyproject.toml`.

## Entry points and public surfaces
- `backend/app/main.py` composes the FastAPI app, CORS policy, and routers for health, auth, dashboard, goal types, goals, notifications, and payment.
- `backend/app/routes/goals.py` owns goal CRUD, goal-type listing, proof submission, and notification side effects for goal creation, proof receipt, verified, and failed transitions.
- `backend/app/goal_types/registry.py` auto-discovers sub-packages under `app.goal_types`, validates that each exports a `GoalTypeBase` instance named `goal_type`, and exposes `list_types()`, `get_type(name)`, and `get_celery_include_modules()`.
- `backend/app/core/celery_app.py` wires Celery to Redis and schedules deadline checks every 60 seconds.
- `backend/cli/main.py` exposes browser-based OAuth login, a debug-only dev-token helper, goal commands, dashboard commands, and notification commands.

## Data and contracts
- `backend/app/schemas/goal.py` and `backend/app/models/goal.py` both still hard-code the allowed goal types to `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo`.
- Goal criteria live in `goal_criteria.criteria_data` as JSONB (`backend/app/models/goal.py`).
- Proof payloads and verification details live in `proof_submissions.proof_data` and `proof_submissions.verification_details` as JSONB (`backend/app/models/proof.py`).
- `backend/app/schemas/proof.py` models proof submission as one flat optional-field JSON body covering YouTube, API endpoint, dev sandbox, and GitHub repo inputs.
- `backend/app/routes/goals.py` flattens the request body with `model_dump(exclude_unset=True)` and calls `goal_type.verify(proof_data, criteria_data)` through the registry.

## Runtime behavior
- `GET /api/goal-types` returns registry metadata including name, description, sample prompts, and criteria schema.
- `POST /api/goals/{goal_id}/submit-proof` supports an immediate `rejected` response from the verifier or stores a `pending` proof submission for later status checks.
- Celery include modules are derived from registered goal types and extended with `app.workers.payments` and `app.workers.deadline`.
- `backend/tests/test_goal_type_smoke.py` proves the registry discovers a newly created goal-type package, and `backend/tests/test_goal_dispatch.py` proves proof submission dispatches through the registry instead of hard-coded branching.

## Integrations
- `backend/app/config.py` exposes environment-driven configuration for PostgreSQL, Redis, Google OAuth, GitHub OAuth, YouTube, Stripe, and Azure Foundry.
- `PROMPT.md` documents that backend and frontend dev servers are expected to already be running and that Celery is optional during most work.

## Current constraints
- The registry is more dynamic than the rest of the backend surface; schema validation and SQL enums still block novel generated goal types from end-to-end creation.
- The inspected proof path is JSON-only; there is no multipart upload or media-ingest contract in the current backend files.
- The CLI depends on the backend API remaining compatible with its goal, dashboard, and notification commands.

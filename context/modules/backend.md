# backend

## What this module is
`backend/` is the Python server-side surface for Sacrifice. It combines the FastAPI API in `backend/app/`, goal-type discovery and Celery worker configuration in `backend/app/goal_types/` and `backend/app/core/`, and a Click CLI in `backend/cli/` that talks to the same HTTP API (`backend/app/main.py`, `backend/app/goal_types/registry.py`, `backend/app/core/celery_app.py`, `backend/cli/main.py`, `backend/pyproject.toml`).

## Entry points and shape files read
- `backend/app/main.py`
- `backend/app/routes/goals.py`
- `backend/app/schemas/goal.py`
- `backend/app/schemas/proof.py`
- `backend/app/models/goal.py`
- `backend/app/goal_types/registry.py`
- `backend/app/core/celery_app.py`
- `backend/app/config.py`
- `backend/cli/main.py`

## Public shape now
- `backend/app/main.py` mounts routers for health, auth, dashboard, goals, notifications, and payment.
- `backend/app/routes/goals.py` exposes `GET /api/goal-types`, goal create/list/detail/update/delete, `POST /api/goals/{goal_id}/submit-proof`, and verification-status endpoints.
- `backend/app/goal_types/registry.py` auto-discovers goal-type packages under `app.goal_types`, exposes `list_types()` and `get_type()`, and derives Celery include paths from registered types.
- `backend/app/core/celery_app.py` includes registered goal-type workers plus deadline and payment workers.
- `backend/cli/main.py` is the local CLI entrypoint and formats goal records and verification status while calling the backend API.

## Current media-pipeline reality
The backend does not yet have a dedicated media upload surface. The FastAPI app mounts no media router, `submit_proof()` accepts a JSON body typed as `ProofSubmissionCreate`, and the proof schema only contains URL, request, repo, and token fields rather than file handles, asset IDs, or multipart forms (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/schemas/proof.py`).

That means the backend currently supports proof metadata submission, not raw device media ingestion. A future physical-world proof flow needs a new backend-owned transport boundary before verification workers can reuse uploaded assets.

## Goal-type contract relevant to physical-world proofs
The backend is partially dynamic today:
- `GET /api/goal-types` and `submit_proof()` both go through the goal-type registry (`backend/app/routes/goals.py`, `backend/app/goal_types/registry.py`).
- Celery worker includes are derived from the same registry rather than a fixed list (`backend/app/core/celery_app.py`, `backend/app/goal_types/registry.py`).

But the backend is also still partially hard-coded:
- `GoalCreate.validate_goal_type()` only accepts `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` (`backend/app/schemas/goal.py`).
- `Goal.goal_type` and `GoalCriteria.criteria_type` persist fixed enum values in the database model (`backend/app/models/goal.py`).
- `ProofSubmissionCreate` has no general media reference field yet, so new proof types still need schema work even though verification dispatch is registry-based (`backend/app/schemas/proof.py`, `backend/app/routes/goals.py`).

## Integration edges
- Owns HTTP contracts consumed by the Expo client and CLI (`backend/app/main.py`, `backend/cli/main.py`, `frontend/services/api.ts`).
- Owns CORS and local-environment defaults that affect frontend/mobile development (`backend/app/main.py`, `backend/app/config.py`).
- Owns the proof submission boundary and the handoff from accepted proof payloads into async verification (`backend/app/routes/goals.py`, `backend/app/core/celery_app.py`).

## Change guidance
For camera-capture work, introduce one backend upload contract that can be shared by future sensor-based goal types, then let goal-type verification consume normalized media references. Do not bolt file ingestion directly into a single future proof type while the rest of the backend still depends on JSON proof payloads and fixed database enums (`backend/app/routes/goals.py`, `backend/app/schemas/proof.py`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py`).
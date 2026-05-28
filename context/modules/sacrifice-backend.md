# sacrifice-backend

## Responsibility
The backend owns the authoritative goal and proof contracts. It mounts the HTTP routers, validates goal creation, discovers goal-type plugins, stores proof submissions, and wires Celery for asynchronous work (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/core/celery_app.py`).

## Key files
- `backend/app/main.py` — FastAPI entrypoint, mounted routers, and explicit CORS allowlist.
- `backend/app/routes/goals.py` — goal CRUD, `/api/goal-types`, proof submission, and verification-status polling.
- `backend/app/schemas/goal.py` — request validation for goal creation and updates.
- `backend/app/schemas/proof.py` — flat proof submission schema covering YouTube, API endpoint, dev sandbox, and GitHub repo bodies.
- `backend/app/models/goal.py` — SQLAlchemy enums for `goal_type`, `criteria_type`, and goal lifecycle status.
- `backend/app/models/proof.py` — JSONB persistence for `proof_data` and `verification_details`.
- `backend/app/goal_types/registry.py` — auto-discovery and lookup of goal-type packages.
- `backend/app/config.py` — local defaults for Postgres, Redis, OAuth, Stripe, YouTube, Docker, and Azure Foundry.

## Current runtime shape
`main.py` mounts health, auth, dashboard, goal-types, goals, notifications, and payment routers. Goal creation goes through `GoalCreate`, which still validates only four goal types. Proof submission goes through `POST /api/goals/{id}/submit-proof`, which looks up the plugin from the registry, calls `verify()` on the flattened request body, stores a pending `ProofSubmission`, and then best-effort invokes `dispatch_verification()` if present (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/schemas/goal.py`, `backend/app/schemas/proof.py`).

## Generator-direction implications
- The backend already has a filesystem discovery seam for generated goal packages under `backend/app/goal_types/`.
- The backend does **not** yet have a fully dynamic creation seam. A new plugin package is not enough; schema validation and DB enums also need to accept the new type (`backend/app/schemas/goal.py`, `backend/app/models/goal.py`).
- The proof contract is broad but still JSON-only. The generic JSONB storage in `ProofSubmission` is helpful for generated modules, but there is no upload/media contract for camera-native proofs (`backend/app/models/proof.py`, `backend/app/schemas/proof.py`).
- The route's real invocation pattern matters: generated verifiers must handle the flattened route payload or the route has to be refactored to honor `submit_proof()` first (`backend/app/routes/goals.py`, `backend/app/goal_types/base.py`).
- Celery include naming is not fully aligned with concrete worker names, so explicit plugin dispatch is safer than inferred worker imports (`backend/app/goal_types/registry.py`, `backend/app/goal_types/youtube_video/__init__.py`, `backend/app/goal_types/api_endpoint/__init__.py`, `backend/app/workers`).

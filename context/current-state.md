# Current State

## Active architectural decisions
Sacrifice currently uses a single FastAPI app as its backend composition point. `backend/app/main.py` assembles routers for health, auth, dashboard, goal types, goals, notifications, and payment, and also keeps a legacy GitHub callback redirect so an older callback path still reaches `/api/auth/github/callback`.

Goal-type extensibility currently enters the system at verification time through filesystem discovery. `backend/app/goal_types/registry.py` scans sub-packages under `app.goal_types`, registers packages that expose a module-level `goal_type` instance derived from `GoalTypeBase`, and derives Celery include modules from the discovered set. `backend/app/routes/goals.py` then resolves `registry.get_type(goal.goal_type)` and calls `verify(proof_data, criteria_data)` instead of hard-coded `if/elif` branching. The backend test suite explicitly checks both auto-discovery and route-level registry dispatch (`backend/tests/test_goal_type_smoke.py`, `backend/tests/test_goal_dispatch.py`).

The rest of the goal pipeline is still fixed-shape around four built-in types. `backend/app/schemas/goal.py` validates `goal_type` against `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo`; `backend/app/models/goal.py` uses the same fixed SQL enum; and `frontend/screens/GoalCreateScreen.tsx` hardcodes the same union and selector metadata. A newly generated goal-type package can therefore be discovered by the registry without yet being creatable end-to-end through the existing API and mobile form.

Proof submission is currently a JSON document pipeline. `backend/app/schemas/proof.py` defines a flat optional-field model, `backend/app/routes/goals.py` flattens the incoming proof payload with `model_dump(exclude_unset=True)`, and `backend/app/models/proof.py` stores `proof_data` and `verification_details` as JSONB. The Expo client mirrors that shape: `frontend/services/api.ts` always sends JSON bodies, and `frontend/screens/ProofSubmissionScreen.tsx` only collects a YouTube URL for the current YouTube proof flow.

The frontend is a single Expo app with handwritten navigation. `frontend/App.tsx` renders one screen at a time based on `currentScreen`, and `frontend/hooks/useNavigation.tsx` maintains a simple screen state plus history stack instead of using React Navigation. Authentication gates the whole app through `AuthProvider`, and the app performs a startup health check against the backend API (`frontend/App.tsx`).

Async/background processing exists as configuration, but is not always running during normal repo work. `backend/app/core/celery_app.py` configures Celery against Redis, includes goal-type worker modules plus payments and deadline workers, and schedules `app.workers.deadline.check_deadlines` every 60 seconds. `PROMPT.md` says the Celery worker is not running by default and should only be started when the task genuinely needs it.

Operational machine migration lives in `scripts/migration/`. The README documents a bundle step that preserves `software-factory/.env`, `software-factory/state/factory.db`, `sacrifice/.env`, and a PostgreSQL dump, plus a bootstrap step that installs prerequisites, clones repos, restores state, recreates Redis empty, runs Alembic, and smoke-checks the factory setup (`scripts/migration/README.md`).

## Module map

| Module | Root | Responsibility | Key files |
| --- | --- | --- | --- |
| Backend | `backend/` | FastAPI API, data model, goal-type registry, Celery config, and Click CLI | `backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/goal_types/registry.py`, `backend/app/models/goal.py`, `backend/cli/main.py` |
| Frontend | `frontend/` | Expo client, auth shell, manual navigation, goal creation, proof submission, and API transport | `frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/screens/GoalCreateScreen.tsx`, `frontend/screens/ProofSubmissionScreen.tsx`, `frontend/services/api.ts` |
| Migration scripts | `scripts/migration/` | Cross-machine backup/bootstrap for Sacrifice plus preserved software-factory state | `scripts/migration/README.md`, `scripts/migration/bootstrap.sh`, `scripts/migration/bundle.sh` |

## Current constraints
- Generated goal types currently stop at the backend registry seam unless the frontend union, backend schema validation, and SQL enums are widened.
- Camera-based proof capture is not present in the inspected app surface: there is no camera plugin in `frontend/app.json`, no multipart/file upload helper in `frontend/services/api.ts`, and no file-upload proof schema in `backend/app/schemas/proof.py`.
- The current YouTube proof screen is specialized to a pasted `youtube_url` and polls verification status through the API (`frontend/screens/ProofSubmissionScreen.tsx`, `frontend/services/api.ts`).
- CLI login and `dev-token` workflows depend on backend auth behavior, and the development token path only works while backend debug mode is enabled (`backend/cli/main.py`, `backend/app/config.py`).

# Current State

## Active architectural decisions
Sacrifice currently runs as one FastAPI API, one Celery worker-and-beat layer, one Click CLI, and one Expo client. The API mounts health, auth, dashboard, goals, notifications, and payment routers, while both the CLI and the frontend talk to that HTTP surface (`backend/app/main.py`, `backend/app/core/celery_app.py`, `backend/cli/main.py`, `frontend/App.tsx`, `frontend/services/api.ts`).

Goal creation is still frontend-led and type-first. `GoalCreateScreen` hard-codes a `GoalType` union with four options, builds criteria objects in the client, and posts those typed payloads to `POST /api/goals`; the backend then validates the same four names in `GoalCreate` and persists matching enum values in `Goal` (`frontend/screens/GoalCreateScreen.tsx`, `backend/app/routes/goals.py`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py`).

Proof submission is still separate from creation and still non-native-capture. `App.tsx` routes video proof to `ProofSubmissionScreen`, that screen asks for a pasted YouTube URL, and the shared frontend API client serializes proof payloads as JSON (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/screens/ProofSubmissionScreen.tsx`, `frontend/services/api.ts`).

The frontend transport layer is JSON-only today. `request()` always adds `Content-Type: application/json`, and every proof helper in `services/api.ts` calls `JSON.stringify(body)` before posting to the backend (`frontend/services/api.ts`). There is no shared `FormData`, binary upload, or pre-signed upload helper in the files read.

The backend has moved verification dispatch toward a registry, but not all layers are dynamic yet. `GET /api/goal-types` lists registered goal types from `app.goal_types.registry`, `submit_proof()` resolves the goal type from that registry and flattens the submitted proof model to JSON, and Celery includes goal-type worker modules through `get_celery_include_modules()` (`backend/app/routes/goals.py`, `backend/app/goal_types/registry.py`, `backend/app/core/celery_app.py`). Creation validation and persistence still hard-code the currently allowed type set in Pydantic and SQLAlchemy enums (`backend/app/schemas/goal.py`, `backend/app/models/goal.py`).

There is no backend media upload pipeline in the files read. The FastAPI app does not mount a media router, `submit_proof()` accepts a Pydantic body rather than `UploadFile` or multipart form fields, and `ProofSubmissionCreate` only models URL, request, repo, and token fields (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/schemas/proof.py`).

For upcoming physical-world proof work, treat camera capture and media upload as shared infrastructure to add beneath goal types, not as one-off logic inside a single proof screen. That direction follows from the current duplication in proof flows and from the complete absence of reusable capture or upload primitives today (`frontend/hooks/useNavigation.tsx`, `frontend/screens/ProofSubmissionScreen.tsx`, `frontend/services/api.ts`, `backend/app/routes/goals.py`, `frontend/app.json`).

## Module map

| Module | Path | Responsibility now | Media-pipeline relevance |
| --- | --- | --- | --- |
| backend | `backend/` | FastAPI API, goal-type registry, Celery configuration, workers, and CLI access to the same HTTP surface | Owns any future upload contract, proof metadata persistence, and verification handoff (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/schemas/proof.py`, `backend/app/goal_types/registry.py`, `backend/app/core/celery_app.py`, `backend/cli/main.py`) |
| frontend | `frontend/` | Expo app shell, local navigation, goal creation, proof submission screens, and shared API client | Owns capture-entry UX, media review UX, and client upload helpers once they exist (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/screens/GoalCreateScreen.tsx`, `frontend/screens/ProofSubmissionScreen.tsx`, `frontend/services/api.ts`) |
| mobile | Native-facing config inside `frontend/` | Expo managed app configuration for iOS and Android alongside the shared client code | Owns native plugins, permissions, and device capability declarations for camera/media features (`frontend/app.json`, `frontend/package.json`, `frontend/AGENTS.md`) |

## Current constraints
- The Expo app has no camera-specific dependency or plugin configuration today. `package.json` does not list a camera/media package, and `app.json` only enables datetime picker, secure store, and web browser plugins (`frontend/package.json`, `frontend/app.json`).
- The navigation state has no capture, recorder, or media-review screen; the current proof surfaces remain `proof-submission`, `api-endpoint-proof-submission`, and `dev-sandbox-proof-submission` (`frontend/hooks/useNavigation.tsx`, `frontend/App.tsx`).
- The backend exposes no upload endpoint or media identifier contract yet, so adding a physical-world proof flow requires more than a new goal type; it also requires a new transport and storage boundary (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/schemas/proof.py`).
- Goal type expansion is still partially cross-cutting because the registry is dynamic but the request validator and database enums still hard-code allowed values (`backend/app/goal_types/registry.py`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py`).
- Local defaults remain localhost-first and integration-heavy: OAuth, Stripe, YouTube, Docker, Redis, and Postgres all depend on environment configuration even though code-level defaults exist (`backend/app/config.py`, `frontend/services/api.ts`).
- Frontend and mobile work should stay within the Expo 54 ecosystem explicitly called out by repository guidance (`frontend/AGENTS.md`, `frontend/package.json`).

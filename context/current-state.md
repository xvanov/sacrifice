# Current State

## Active architectural decisions
Sacrifice currently runs through one FastAPI API, one Celery worker layer, one Click CLI, and one Expo client. The API mounts health, auth, dashboard, goal-types, goals, notifications, and payment routers; both the CLI and the frontend consume that HTTP surface rather than talking to the database or workers directly (`backend/app/main.py`, `backend/app/core/celery_app.py`, `backend/cli/main.py`, `frontend/App.tsx`, `frontend/services/api.ts`).

The user-facing creation flow is still typed and screen-driven, not chat-driven. `App.tsx` switches between named screens from `useNavigation.tsx`, and that screen union includes `home`, `dashboard`, `goal-create`, `goal-detail`, three proof submission variants, `notifications`, and `login` only. No `chat` screen or chat state shape appears in the frontend navigation surface that was read (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`).

Goal creation is still frontend-led. `GoalCreateScreen` assembles a typed `criteria` payload from form state and sends a fully formed goal body to `POST /api/goals`; the backend `GoalCreate` schema then validates the same four supported goal types: `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` (`frontend/screens/GoalCreateScreen.tsx`, `frontend/services/api.ts`, `backend/app/routes/goals.py`, `backend/app/schemas/goal.py`).

The plugin system is only partially the source of truth today. `GET /api/goal-types` lists registered plugins from `app.goal_types.registry`, `submit_proof()` resolves a goal type from that registry, and Celery includes worker modules by asking the registry for include paths. But creation validation still hard-codes the allowed type set in `GoalCreate`, so a future generator cannot stop at plugin creation alone (`backend/app/routes/goals.py`, `backend/app/goal_types/registry.py`, `backend/app/core/celery_app.py`, `backend/app/schemas/goal.py`, `backend/app/goal_types/base.py`).

Proof submission remains JSON-first and artifact-specific. The shared request helper always sends `Content-Type: application/json`; `ProofSubmissionScreen` asks for a pasted YouTube URL; and `ProofSubmissionCreate` models URL, request, repo, branch, test command, env var, and token fields rather than file uploads or media handles (`frontend/services/api.ts`, `frontend/screens/ProofSubmissionScreen.tsx`, `backend/app/schemas/proof.py`).

There is no implemented chat-factory generation path in the code read for this scan. The frontend has no chat route or API method, FastAPI mounts no chat router, and the backend creation endpoint expects `goal_type` plus typed `criteria` to already be decided before the request arrives (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/services/api.ts`, `backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/schemas/goal.py`).

There is also no implemented camera-capture pipeline in the current repo surfaces. `frontend/package.json` does not include a camera/media dependency, `frontend/app.json` declares no camera plugin, and the proof transport remains JSON-only, so physical-world verification is blocked by missing client capture and missing backend upload contracts, not just by a missing goal-type plugin (`frontend/package.json`, `frontend/app.json`, `frontend/services/api.ts`, `backend/app/schemas/proof.py`).

## Module map

| Module | Primary files | Responsibility now | D010 relevance |
| --- | --- | --- | --- |
| sacrifice-backend | `backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/schemas/goal.py`, `backend/app/schemas/proof.py`, `backend/app/core/celery_app.py` | Owns HTTP entry points, schema validation, proof persistence, and worker dispatch | Any chat-factory flow has to terminate here as a standard goal create request and proof workflow |
| sacrifice-chat | `frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/services/api.ts` | Currently an absence boundary rather than an implemented module | D010 starts by documenting what is missing: no chat screen, no chat API client, no chat router |
| goal-type-plugins | `backend/app/goal_types/base.py`, `backend/app/goal_types/registry.py`, `backend/app/goal_types/youtube_video/__init__.py` | Defines plugin contract, auto-discovery, and a concrete plugin example | The generator direction can target this seam, but must account for create-time hard-coded types elsewhere |
| camera-capture-pipeline | `frontend/screens/ProofSubmissionScreen.tsx`, `frontend/package.json`, `frontend/app.json`, `backend/app/schemas/proof.py` | Not yet implemented as reusable infrastructure | Pushup-style or phone-camera verification depends on filling this gap across both client and backend |
| factory-directions | `PRD.md`, `PROMPT.md`, `activity.md`, `context/architecture-diagrams.md` | Holds product intent, task-runner guidance, implementation history, and architecture summaries | D010 should reconcile any proposed chat-generator flow with these current-state docs before code changes |

## Current constraints
- The current navigation and app shell have nowhere to render a chat-first goal creation experience yet (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`).
- The API client and backend transport are both JSON-oriented today, so any capture or generation flow that needs richer payloads introduces a new contract boundary (`frontend/services/api.ts`, `backend/app/routes/goals.py`, `backend/app/schemas/proof.py`).
- Registry discovery is dynamic for listing and proof dispatch, but create-time validation is still fixed to four values, so plugin generation alone is insufficient (`backend/app/goal_types/registry.py`, `backend/app/schemas/goal.py`).
- The current proof UX is split by artifact type and includes a YouTube-specific screen; there is no shared capture component or shared proof composer yet (`frontend/App.tsx`, `frontend/screens/ProofSubmissionScreen.tsx`).
- The managed Expo app still lacks camera/media packages and camera plugin configuration, and frontend work is explicitly constrained to the Expo 54 documentation line called out by `frontend/AGENTS.md` (`frontend/package.json`, `frontend/app.json`, `frontend/AGENTS.md`).
- `PRD.md` still describes the implemented MVP in terms of YouTube, API endpoint, and dev sandbox proof models; `PROMPT.md` gives generic task execution instructions but does not define a current chat-factory route or generator architecture (`PRD.md`, `PROMPT.md`).

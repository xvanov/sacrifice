# Current State

## Active architectural decisions
Sacrifice runs as four primary runtime surfaces: a FastAPI API, a Celery worker and beat layer, a Click CLI, and a single Expo client. The API mounts health, auth, dashboard, goals, notifications, and payment routers from `backend/app/main.py`, and both the CLI and frontend talk to that HTTP surface (`backend/app/main.py`, `backend/cli/client.py`, `frontend/services/api.ts`).

Goal creation is currently frontend-led and type-first. The home screen routes `+ New` into `goal-create`, `App.tsx` renders `GoalCreateScreen` for that screen name, and `GoalCreateScreen` keeps a single local form with a hard-coded `GoalType` union plus conditional subforms for YouTube, API endpoint, dev sandbox, and GitHub repo criteria (`frontend/screens/HomeScreen.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/App.tsx`, `frontend/screens/GoalCreateScreen.tsx`).

The backend expects goal creation requests to arrive already classified. `GoalCreate` validates `goal_type` against a fixed allowed set, `Goal` and `GoalCriteria` persist matching enum values, and `POST /api/goals` stores the provided type and criteria after auth and notification side effects (`backend/app/schemas/goal.py`, `backend/app/models/goal.py`, `backend/app/routes/goals.py`).

There is no dedicated chat creation surface in the files read. The FastAPI app does not mount a chat router, the frontend navigation union does not include a chat screen, and the shared frontend API client does not expose a chat or goal-type-catalog call (`backend/app/main.py`, `frontend/hooks/useNavigation.tsx`, `frontend/services/api.ts`).

Proof submission remains separate from creation and stays type-specific. `App.tsx` renders distinct proof submission screens, and `POST /api/goals/{goal_id}/submit-proof` branches on `goal.goal_type` to validate payloads and dispatch the matching Celery task (`frontend/App.tsx`, `backend/app/routes/goals.py`, `backend/app/core/celery_app.py`).

Background enforcement is time-based. Celery beat runs `check_deadlines` every 60 seconds in UTC, the deadline worker fails expired active goals and late `pending_review` goals after a five-minute grace period, recurring goals clone the next instance, and failed goals hand off to payment processing (`backend/app/core/celery_app.py`, `backend/app/workers/deadline.py`).

The activity log shows the implementation is ahead of the original PRD in at least one user-facing area: the PRD centers YouTube, API endpoint, and dev sandbox verification, while the current frontend, backend, and CLI all include a fourth `github_repo` goal type (`PRD.md`, `backend/app/routes/goals.py`, `frontend/screens/GoalCreateScreen.tsx`, `backend/cli/main.py`).

## Module map

| Module | Path | Responsibility now | Evidence read |
| --- | --- | --- | --- |
| backend-app | `backend/app/` | FastAPI settings, CORS, router composition, goal CRUD, proof dispatch, payments, dashboard, and notifications | `backend/app/main.py`, `backend/app/config.py`, `backend/app/routes/goals.py`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py` |
| backend-workers | `backend/app/core/` and `backend/app/workers/` | Celery queue topology, deadline enforcement, recurrence, and payment handoff | `backend/app/core/celery_app.py`, `backend/app/workers/deadline.py` |
| backend-cli | `backend/cli/` | Authenticated command-line access to the backend goal, dashboard, and notification APIs | `backend/cli/main.py`, `backend/cli/client.py` |
| frontend | `frontend/` | Expo app with auth, local screen switching, goal list/detail, goal creation, dashboard, notifications, and proof submission | `frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/services/api.ts`, `frontend/package.json` |
| goal-creation | `frontend/screens/GoalCreateScreen.tsx` plus `POST /api/goals` | Type-first creation flow that assembles `goal_type` and typed `criteria` on the client before sending them to the backend | `frontend/screens/HomeScreen.tsx`, `frontend/screens/GoalCreateScreen.tsx`, `backend/app/routes/goals.py`, `backend/app/schemas/goal.py` |
| chat | no dedicated source path yet | Not implemented as a separate current module; creation still enters the typed goal screen and there is no mounted chat API surface | `backend/app/main.py`, `frontend/hooks/useNavigation.tsx`, `frontend/App.tsx`, `frontend/services/api.ts` |

## Current constraints
- Adding or changing a goal type is still cross-cutting: frontend form state, backend request validation, database enums, proof submission branching, Celery includes, and client helpers all encode the type set directly (`frontend/screens/GoalCreateScreen.tsx`, `backend/app/schemas/goal.py`, `backend/app/models/goal.py`, `backend/app/routes/goals.py`, `backend/app/core/celery_app.py`, `frontend/services/api.ts`, `backend/cli/main.py`).
- The current creation path does not expose a free-text "describe my goal" entrypoint or a backend classification endpoint; any AI-first chat flow would be replacing work, not extending an already-mounted surface (`frontend/screens/HomeScreen.tsx`, `frontend/hooks/useNavigation.tsx`, `backend/app/main.py`, `frontend/services/api.ts`).
- Proof submission should be treated separately from goal creation because the app currently routes proof flows through dedicated screens and backend branches after a goal already has a stored `goal_type` (`frontend/App.tsx`, `backend/app/routes/goals.py`).
- Local defaults remain localhost-first and integration-heavy: OAuth, Stripe, YouTube, Docker, and Azure Foundry all depend on environment configuration even though code-level defaults exist (`backend/app/config.py`, `frontend/services/api.ts`).
- Frontend work should stay within the Expo 54 ecosystem explicitly called out by the repo guidance (`frontend/AGENTS.md`, `frontend/package.json`).

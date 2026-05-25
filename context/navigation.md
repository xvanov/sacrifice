# Navigation

## When working on overall repository shape
- Context files:
  - `context/project.md` — slow-changing identity, stack, and top-level layout
  - `context/current-state.md` — current architecture and module map
  - `context/architecture-diagrams.md` — current system flow and primary interaction path
- Relevant code paths:
  - `backend/pyproject.toml` — backend runtime, CLI entrypoint, and Python dependencies
  - `frontend/package.json` — Expo app dependencies and scripts
  - `backend/app/main.py` — FastAPI entrypoint and router composition
  - `backend/app/core/celery_app.py` — Celery topology and beat schedule
  - `backend/cli/main.py` — CLI surface and command groups
  - `frontend/App.tsx` — frontend entrypoint and screen switching

## When working on the backend API
- Context files:
  - `context/current-state.md` — router composition, storage, and integration constraints
  - `context/modules/backend-app.md` — FastAPI entrypoint, settings, database, and goal-facing interfaces
  - `context/glossary.md` — domain terms used across goals, pledges, proof, and charities
- Relevant code paths:
  - `backend/app/main.py`
  - `backend/app/config.py`
  - `backend/app/database.py`
  - `backend/app/core/dependencies.py`
  - `backend/app/routes/auth.py`
  - `backend/app/routes/goals.py`
  - `backend/app/routes/dashboard.py`
  - `backend/app/routes/notifications.py`
  - `backend/app/routes/payment.py`
  - `backend/app/schemas/`
  - `backend/app/services/`
  - `backend/app/models/`

## When working on background verification, deadlines, or payments
- Context files:
  - `context/current-state.md` — queueing model and current constraints
  - `context/modules/backend-workers.md` — Celery includes, beat schedule, recurrence, and payment/disbursement notes
  - `context/architecture-diagrams.md` — worker placement in the current system
- Relevant code paths:
  - `backend/app/core/celery_app.py`
  - `backend/app/workers/deadline.py`
  - `backend/app/workers/payments.py`
  - `backend/app/workers/youtube.py`
  - `backend/app/workers/api_check.py`
  - `backend/app/workers/dev_sandbox.py`
  - `backend/app/workers/github_repo.py`
  - `backend/app/models/goal.py`
  - `backend/app/models/payment.py`
  - `backend/app/routes/goals.py`

## When working on the CLI
- Context files:
  - `context/project.md` — where the CLI fits in the product
  - `context/modules/backend-cli.md` — command groups, auth flow, and API client shape
  - `context/current-state.md` — shared constraints with the backend API
- Relevant code paths:
  - `backend/cli/main.py`
  - `backend/cli/client.py`
  - `backend/app/routes/auth.py`
  - `backend/app/routes/goals.py`
  - `backend/app/routes/dashboard.py`
  - `backend/app/routes/notifications.py`

## When working on the Expo client
- Context files:
  - `context/project.md` — frontend stack and repo placement
  - `context/modules/frontend.md` — screen switching, API client behavior, and frontend-specific constraints
  - `context/glossary.md` — user-facing domain terms reflected in screens and actions
- Relevant code paths:
  - `frontend/App.tsx`
  - `frontend/hooks/useAuth.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/services/api.ts`
  - `frontend/services/auth.ts`
  - `frontend/screens/`
  - `frontend/components/NotificationBell.tsx`

## When working on goals, proof submission, or verification status
- Context files:
  - `context/current-state.md` — currently implemented proof paths and async model
  - `context/modules/backend-app.md` — goal endpoints and proof dispatch
  - `context/modules/backend-workers.md` — verification execution and deadline handling
  - `context/modules/frontend.md` — frontend API methods and current screens
- Relevant code paths:
  - `backend/app/routes/goals.py`
  - `backend/app/schemas/goal.py`
  - `backend/app/schemas/proof.py`
  - `backend/app/models/goal.py`
  - `backend/app/models/proof.py`
  - `backend/app/services/goal.py`
  - `backend/app/workers/youtube.py`
  - `backend/app/workers/api_check.py`
  - `backend/app/workers/dev_sandbox.py`
  - `backend/app/workers/github_repo.py`
  - `frontend/screens/GoalCreateScreen.tsx`
  - `frontend/screens/GoalDetailScreen.tsx`
  - `frontend/screens/ProofSubmissionScreen.tsx`
  - `frontend/screens/ApiEndpointSubmissionScreen.tsx`
  - `frontend/screens/DevSandboxSubmissionScreen.tsx`
  - `frontend/services/api.ts`
  - `backend/cli/main.py`

## When working on notifications or dashboard behavior
- Context files:
  - `context/current-state.md` — feature coverage summary from the implementation log
  - `context/modules/backend-app.md` — HTTP surfaces used by these features
  - `context/modules/frontend.md` — client-side consumption of dashboard and notification APIs
- Relevant code paths:
  - `backend/app/routes/dashboard.py`
  - `backend/app/routes/notifications.py`
  - `backend/app/models/notification.py`
  - `backend/app/services/notification.py`
  - `frontend/screens/DashboardScreen.tsx`
  - `frontend/screens/NotificationListScreen.tsx`
  - `frontend/components/NotificationBell.tsx`
  - `frontend/services/api.ts`
  - `backend/cli/main.py`

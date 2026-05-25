# Navigation

## When working on overall repository shape
- Context files:
  - `context/project.md` — slow-changing identity, stack, and top-level layout
  - `context/current-state.md` — active architectural decisions and module map
  - `context/modules/backend-app.md` — backend HTTP surface
  - `context/modules/frontend.md` — frontend shell and screen switching
- Relevant code paths:
  - `backend/pyproject.toml`
  - `frontend/package.json`
  - `backend/app/main.py`
  - `backend/app/core/celery_app.py`
  - `backend/cli/main.py`
  - `frontend/App.tsx`

## When working on backend HTTP behavior
- Context files:
  - `context/current-state.md` — router composition and creation constraints
  - `context/modules/backend-app.md` — FastAPI entrypoint, settings, and goal-facing interfaces
  - `context/modules/goal-creation.md` — current creation payload shape
  - `context/modules/chat.md` — current absence of chat endpoints and screen state
- Relevant code paths:
  - `backend/app/main.py`
  - `backend/app/config.py`
  - `backend/app/routes/auth.py`
  - `backend/app/routes/goals.py`
  - `backend/app/routes/dashboard.py`
  - `backend/app/routes/notifications.py`
  - `backend/app/routes/payment.py`
  - `backend/app/schemas/goal.py`
  - `backend/app/models/goal.py`

## When working on background verification, deadlines, or payment enforcement
- Context files:
  - `context/current-state.md` — async enforcement model and proof separation
  - `context/modules/backend-workers.md` — Celery includes, beat schedule, recurrence, and payment notes
  - `context/modules/backend-app.md` — proof dispatch entrypoint
- Relevant code paths:
  - `backend/app/core/celery_app.py`
  - `backend/app/workers/deadline.py`
  - `backend/app/workers/payments.py`
  - `backend/app/routes/goals.py`
  - `backend/app/models/goal.py`

## When working on the CLI
- Context files:
  - `context/project.md` — where the CLI fits in the product
  - `context/current-state.md` — shared backend constraints
  - `context/modules/backend-cli.md` — command groups and API client behavior
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
  - `context/current-state.md` — app shell and creation flow decisions
  - `context/modules/frontend.md` — screen switching and API behavior
  - `context/modules/goal-creation.md` — current typed creation UI
- Relevant code paths:
  - `frontend/App.tsx`
  - `frontend/hooks/useAuth.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/services/api.ts`
  - `frontend/services/auth.ts`
  - `frontend/screens/HomeScreen.tsx`
  - `frontend/screens/GoalCreateScreen.tsx`

## When working on goal creation
- Context files:
  - `context/current-state.md` — current type-first architecture
  - `context/modules/goal-creation.md` — end-to-end creation flow today
  - `context/modules/chat.md` — current absence of a chat replacement surface
  - `context/modules/frontend.md` — frontend shell and navigation context
- Relevant code paths:
  - `frontend/screens/HomeScreen.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/App.tsx`
  - `frontend/screens/GoalCreateScreen.tsx`
  - `frontend/services/api.ts`
  - `backend/app/routes/goals.py`
  - `backend/app/schemas/goal.py`
  - `backend/app/models/goal.py`

## When working on chat or goal-type matching
- Context files:
  - `context/current-state.md` — explicit note that chat is not mounted yet
  - `context/modules/chat.md` — current chat gap and likely integration edges
  - `context/modules/goal-creation.md` — existing flow being replaced
  - `context/modules/backend-app.md` — router composition and request validation
- Relevant code paths:
  - `backend/app/main.py`
  - `backend/app/routes/goals.py`
  - `backend/app/schemas/goal.py`
  - `backend/app/models/goal.py`
  - `frontend/screens/HomeScreen.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/App.tsx`
  - `frontend/services/api.ts`
  - `frontend/screens/GoalCreateScreen.tsx`
- Notes:
  - The current files read for this scan show no dedicated chat screen, chat API client method, or mounted chat router.

## When working on proof submission, notifications, or dashboard behavior
- Context files:
  - `context/current-state.md` — proof separation and feature coverage
  - `context/modules/backend-app.md` — goal, dashboard, and notification endpoints
  - `context/modules/backend-workers.md` — verification execution and deadline handling
  - `context/modules/frontend.md` — client-side consumption of these APIs
- Relevant code paths:
  - `backend/app/routes/goals.py`
  - `backend/app/routes/dashboard.py`
  - `backend/app/routes/notifications.py`
  - `backend/app/workers/deadline.py`
  - `frontend/screens/ProofSubmissionScreen.tsx`
  - `frontend/screens/ApiEndpointSubmissionScreen.tsx`
  - `frontend/screens/DevSandboxSubmissionScreen.tsx`
  - `frontend/screens/DashboardScreen.tsx`
  - `frontend/screens/NotificationListScreen.tsx`
  - `frontend/components/NotificationBell.tsx`
  - `frontend/services/api.ts`

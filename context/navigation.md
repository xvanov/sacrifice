# Navigation

## When working on the backend API
- Context: `context/project.md`, `context/current-state.md`, `context/modules/backend.md`
- Open these code paths first:
  - `backend/app/main.py`
  - `backend/app/config.py`
  - `backend/app/routes/goals.py`
  - `backend/app/routes/auth.py`
  - `backend/app/core/celery_app.py`

## When working on auth
- Context: `context/project.md`, `context/current-state.md`, `context/modules/backend.md`, `context/modules/frontend.md`, `context/modules/cli.md`
- Open these code paths first:
  - `backend/app/routes/auth.py`
  - `backend/app/core/dependencies.py`
  - `backend/app/services/auth.py`
  - `frontend/hooks/useAuth.tsx`
  - `frontend/services/auth.ts`
  - `frontend/screens/LoginScreen.tsx`
  - `backend/cli/main.py`
  - `backend/cli/client.py`

## When working on goals and proof submission
- Context: `prd.md`, `context/current-state.md`, `context/modules/backend.md`, `context/modules/frontend.md`
- Open these code paths first:
  - `backend/app/routes/goals.py`
  - `backend/app/services/goal.py`
  - `backend/app/models/goal.py`
  - `backend/app/models/proof.py`
  - `backend/app/schemas/goal.py`
  - `backend/app/schemas/proof.py`
  - `frontend/screens/GoalCreateScreen.tsx`
  - `frontend/screens/GoalDetailScreen.tsx`
  - `frontend/screens/ProofSubmissionScreen.tsx`
  - `frontend/screens/ApiEndpointSubmissionScreen.tsx`
  - `frontend/screens/DevSandboxSubmissionScreen.tsx`
  - `frontend/services/api.ts`
  - `frontend/types/index.ts`

## When working on background verification and deadlines
- Context: `prd.md`, `context/current-state.md`, `context/modules/backend.md`
- Open these code paths first:
  - `backend/app/core/celery_app.py`
  - `backend/app/workers/youtube.py`
  - `backend/app/workers/api_check.py`
  - `backend/app/workers/dev_sandbox.py`
  - `backend/app/workers/github_repo.py`
  - `backend/app/workers/deadline.py`
  - `backend/app/workers/payments.py`
  - `backend/app/services/youtube.py`
  - `backend/app/services/llm.py`

## When working on payments and charities
- Context: `prd.md`, `context/current-state.md`, `context/modules/backend.md`, `context/modules/frontend.md`
- Open these code paths first:
  - `backend/app/routes/payment.py`
  - `backend/app/workers/payments.py`
  - `backend/app/models/payment.py`
  - `backend/app/config.py`
  - `frontend/services/api.ts`
  - `frontend/types/index.ts`

## When working on dashboard and notifications
- Context: `context/current-state.md`, `context/modules/backend.md`, `context/modules/frontend.md`
- Open these code paths first:
  - `backend/app/routes/dashboard.py`
  - `backend/app/routes/notifications.py`
  - `backend/app/services/notification.py`
  - `backend/app/models/notification.py`
  - `frontend/screens/DashboardScreen.tsx`
  - `frontend/screens/NotificationListScreen.tsx`
  - `frontend/components/NotificationBell.tsx`
  - `frontend/services/api.ts`
  - `frontend/types/index.ts`

## When working on the frontend shell and navigation
- Context: `context/project.md`, `context/current-state.md`, `context/modules/frontend.md`
- Open these code paths first:
  - `frontend/App.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/hooks/useAuth.tsx`
  - `frontend/screens/HomeScreen.tsx`
  - `frontend/components/index.ts`
  - `frontend/global.css`

## When working on the CLI
- Context: `context/project.md`, `context/current-state.md`, `context/modules/cli.md`, `context/modules/backend.md`
- Open these code paths first:
  - `backend/cli/main.py`
  - `backend/cli/client.py`
  - `backend/pyproject.toml`
  - `Makefile`

## When working on local setup and running the stack
- Context: `context/project.md`, `context/current-state.md`
- Open these code paths first:
  - `Makefile`
  - `backend/pyproject.toml`
  - `backend/app/config.py`
  - `backend/app/core/celery_app.py`
  - `frontend/package.json`
  - `frontend/AGENTS.md`
  - `activity.md`

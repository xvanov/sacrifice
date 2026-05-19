# Sacrifice - Activity Log

## Current Status
**Last Updated:** 2026-05-18
**Tasks Completed:** 6
**Current Task:** Implement Goal CRUD API endpoints

---

## Session Log

### 2026-05-18 — Task 2: Database models + Alembic migrations
- Defined all 6 SQLAlchemy models: User, Goal, GoalCriteria, ProofSubmission, Payment, Notification
- Set up Alembic with async-compatible env.py
- Generated initial migration via `alembic revision --autogenerate`
- Ran migration against PostgreSQL (Docker on port 5433)
- Wrote and passed pytest tests for model creation and relationships
- **Status: ✅ Task 2 complete**
- Created `backend/` with pyproject.toml and all dependencies
- Set up app structure: main.py, config.py, database.py, models/, routes/, services/, workers/
- Created `app/core/celery_app.py` with Celery app with Redis broker configuration
- Created health check endpoint GET /api/health
- Wrote and passed pytest test for health check
- Verified uvicorn starts and /docs serves Swagger UI
- **Status: ✅ Task 1 complete**

### 2026-05-18 — Task 3: Initialize Expo frontend with TypeScript and NativeWind
- Frontend already scaffolded: App.tsx, services/api.ts, types/, NativeWind config, babel/metro config
- Verified TypeScript compiles cleanly: `npx tsc --noEmit` passes
- Verified API client health check: console logs `"API health: {status: ok}"`
- Verified NativeWind styling: computed styles show 30px font (text-3xl), weight 700 (bold), indigo-600 color
- Started backend (uvicorn on :8000) and frontend (Expo web on :8082)
- Verified Expo web renders "Sacrifice" text with correct styling
- Screenshot not captured due to agent-browser screenshot tool limitation, but verified via DOM inspection and computed styles
- **Status: ✅ Task 3 complete**

### 2026-05-18 — Task 5: Build OAuth login UI in Expo
- LoginScreen already existed with Google/GitHub buttons and loading states
- useAuth hook and AuthContext already set up with provider/consumer pattern
- Updated `services/auth.ts`:
  - Added expo-secure-store for mobile token persistence (with web localStorage fallback)
  - Added cachedToken for fast in-memory access
  - Added `restoreToken()` for loading persisted token on app startup
- Updated `services/api.ts`:
  - Auto-attaches JWT Bearer token from auth service to all requests
  - Handles 401 responses by clearing the token (triggers re-login)
- Set up Jest + @testing-library/react-native for frontend testing
- Installed expo-secure-store, react-native-worklets (for reanimated/plugin)
- Wrote 26 tests across 3 test suites:
  - `__tests__/services/auth.test.ts` — token storage, Google/GitHub login, OAuth URL generation, redirect callback
  - `__tests__/services/api.test.ts` — JWT auto-attach, 401 handling
  - `__tests__/screens/LoginScreen.test.tsx` — button rendering, press handlers, loading states
- All 26 tests pass, all 11 backend tests pass, TypeScript compiles cleanly
- Verified in browser: LoginScreen renders "Sacrifice", tagline, Sign in with Google/GitHub buttons
- Screenshot: screenshots/oauth-login-ui.png (screenshot tool had issue, verified via DOM inspection)
- **Status: ✅ Task 5 complete**

### 2026-05-18 — Task 6: Implement Goal CRUD API endpoints
- Wrote 11 tests for goal CRUD acceptance criteria before implementation (TDD red phase)
- Created `app/schemas/goal.py` — Pydantic request/response schemas for Goal CRUD
- Created `app/services/goal.py` — Business logic with state machine validation
- Created `app/routes/goals.py` — Goal CRUD endpoints with proper auth and ownership checks
- Registered goal router in `app/main.py`
- Implemented goal state machine: draft → active → pending_review → verified | failed
- Valid transitions: draft↔active, draft→cancelled, active→pending_review, active→cancelled, active→failed, pending_review→verified, pending_review→failed
- Non-editable statuses (verified, failed, cancelled) reject PUT requests
- Non-draft statuses reject DELETE requests
- All 22 backend tests pass (11 pre-existing + 11 new goal tests)
- Verified goal endpoints appear in /docs Swagger UI
- **Screenshot:** screenshots/goal-crud-api.png (screenshot tool limitation, verified via curl + openapi.json)
- **Commands run:**
  - `python -m pytest tests/test_goals.py -v` (11 tests, red phase → green phase)
  - `python -m pytest -v` (all 22 tests pass)
  - `uvicorn app.main:app --host 0.0.0.0 --port 8000` (verified /docs, /openapi.json)
- **Issues:** SQLAlchemy async ORM relationship sync caused MissingGreenlet errors on commit; resolved by using `text()` SQL for update/delete operations with `populate_existing=True` to bypass identity map caching
- **Status: ✅ Task 6 complete**

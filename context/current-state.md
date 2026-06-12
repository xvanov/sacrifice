# Current State

## Active architectural decisions
Sacrifice still treats goal creation as a typed form flow in the frontend. `App.tsx` routes the `goal-create` screen name to `GoalCreateScreen`, and the navigation union has no chat, conversation, or matcher-specific screen today (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`). Inside `GoalCreateScreen`, the client keeps a local `GoalType` union of `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo`, renders a different sub-form for each type, validates those type-specific inputs, and builds the final `criteria` object in the client before posting to `POST /api/goals` (`frontend/screens/GoalCreateScreen.tsx`).

The current creation contract is reinforced by tests. `GoalCreateScreen.test.tsx` asserts that the screen shows YouTube-specific fields by default, swaps in API and sandbox fields when the user taps the type pills, and submits JSON directly to `/api/goals` with the form-derived payload (`frontend/__tests__/screens/GoalCreateScreen.test.tsx`). Replacing typed goal creation therefore requires updating both the screen and the tests that currently encode its type-first behavior.

The creation screen also owns the payment-method warning and charity lookup. On mount it calls `getPaymentMethods()` and `searchCharities()`, then includes the selected `charity_id` in the same create-goal payload (`frontend/screens/GoalCreateScreen.tsx`, `frontend/services/api.ts`). Any chat-first replacement still has to account for those non-goal-type parts of the flow.

The backend already has one reusable catalog of goal types, but it is not yet a prompt-matching API. `GET /api/goal-types` returns each registered plugin's `name`, `description`, `sample_prompts`, and `criteria_schema` for authenticated users, and the registry auto-discovers packages under `app.goal_types` (`backend/app/routes/goals.py`, `backend/app/goal_types/registry.py`, `backend/tests/test_goal_types_api.py`). In the files read, though, `main.py` mounts no prompt-matching route, `routes/goals.py` exposes no create-from-prompt endpoint beside `POST /api/goals`, and the frontend API client does not call `/api/goal-types` today (`backend/app/main.py`, `backend/app/routes/goals.py`, `frontend/services/api.ts`).

Goal creation on the server is still explicit and type-first. `GoalCreate` requires both `goal_type` and `criteria`, validates `goal_type` against the same four core names, `create_goal()` persists the submitted type and criteria row directly, and the database enums still encode fixed values for `Goal.goal_type` and `GoalCriteria.criteria_type` (`backend/app/schemas/goal.py`, `backend/app/services/goal.py`, `backend/app/models/goal.py`). A chat-driven creation path therefore still has to resolve a concrete `goal_type` plus concrete `criteria` before it can reuse the existing create flow.

LLM usage exists today, but only for proof verification. `app.services.llm` exposes transcript and code-authenticity judges for YouTube and dev-sandbox proof review; there is no prompt-to-goal-type classifier in the files read (`backend/app/services/llm.py`). The plugin catalog exposes enough descriptive metadata for a matcher, but the matcher itself is not present yet.

Proof submission remains separate from creation and should stay separate in this batch. `App.tsx` still routes to the existing proof-submission screens, `api.ts` still posts proof payloads as JSON, and `submit_proof()` remains goal-type-agnostic by dispatching through the registry (`frontend/App.tsx`, `frontend/services/api.ts`, `backend/app/routes/goals.py`).

## Module map

| Module | Path | Responsibility now | Chat-creation relevance |
| --- | --- | --- | --- |
| frontend | `frontend/` | App shell, in-memory navigation, goal creation UI, proof submission entry points, and shared HTTP helpers (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/screens/GoalCreateScreen.tsx`, `frontend/services/api.ts`) | Owns removal of the goal-type picker and typed sub-forms, plus any prompt composer or match-review UX |
| backend | `backend/` | FastAPI routes, plugin registry, goal persistence, proof dispatch, and current LLM verification helpers (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/services/goal.py`, `backend/app/goal_types/registry.py`, `backend/app/services/llm.py`) | Owns any matcher endpoint or service that turns free text into a supported `goal_type` and `criteria` |

## Current constraints
- The current creation screen still assumes the user chooses a goal type up front; it renders four typed sub-forms and builds the `criteria` payload locally (`frontend/screens/GoalCreateScreen.tsx`).
- The shared frontend API client has `createGoal()`, `searchCharities()`, and payment helpers, but no helper for fetching the goal-type catalog or posting a prompt for matching (`frontend/services/api.ts`).
- The backend exposes `/api/goal-types`, but no prompt-matching or prompt-driven creation route is mounted beside `/api/goals` (`backend/app/main.py`, `backend/app/routes/goals.py`).
- Server-side validation and persistence still hard-code the four core types, so a matcher cannot return an arbitrary new registry name without additional schema and database work (`backend/app/schemas/goal.py`, `backend/app/models/goal.py`, `backend/app/services/goal.py`).
- Existing frontend tests encode the typed UX and current request shape, so replacing the creation surface requires rewriting those expectations rather than only changing production code (`frontend/__tests__/screens/GoalCreateScreen.test.tsx`).
- No "no match → generate a new goal type" path exists in the files read; that follow-on factory behavior is not part of the current backend surface (`backend/app/main.py`, `backend/app/routes/goals.py`, `backend/app/services/llm.py`).
- Proof submission screens and proof verification routing already key off the stored `goal_type`; they are downstream of creation and should remain untouched in this batch (`frontend/App.tsx`, `backend/app/routes/goals.py`).

<!-- factory:context-refresh ts=2026-06-12T05:10:35.787990+00:00 after_pr=#122 -->

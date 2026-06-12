# frontend

## What this module is
`frontend/` is the shared client shell for Sacrifice. `App.tsx` mounts the auth and navigation providers, `useNavigation.tsx` owns the in-memory screen union, `GoalCreateScreen.tsx` owns the current goal-creation UX, and `services/api.ts` wraps authenticated JSON fetches to the FastAPI backend (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/screens/GoalCreateScreen.tsx`, `frontend/services/api.ts`).

## Entry points and shape files read
- `frontend/App.tsx`
- `frontend/hooks/useNavigation.tsx`
- `frontend/screens/GoalCreateScreen.tsx`
- `frontend/services/api.ts`
- `frontend/__tests__/screens/GoalCreateScreen.test.tsx`
- `frontend/AGENTS.md`

## Public shape now
`App.tsx` renders screens by matching `currentScreen.name`. The current union includes `goal-create`, but it does not include a chat, conversation, or matcher-review screen. Creation therefore still happens inside the existing `GoalCreateScreen` route rather than a separate assistant flow (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`).

`GoalCreateScreen` is explicitly type-first today. It stores `goal_type` in local form state, renders four selectable options (`youtube_video`, `api_endpoint`, `dev_sandbox`, `github_repo`), shows a different form fragment for each one, validates those fields locally, and builds the final `criteria` object in `handleSubmit()` before calling `api.createGoal()` (`frontend/screens/GoalCreateScreen.tsx`). The same screen also owns title, deadline, pledge amount, charity selection, and the payment-method warning, so replacing the goal-type picker does not eliminate the rest of the screen's responsibilities (`frontend/screens/GoalCreateScreen.tsx`, `frontend/services/api.ts`).

The current client API surface is thin and JSON-only. `request()` always sends `Content-Type: application/json`; `createGoal()` posts directly to `/api/goals`; `searchCharities()` and `getPaymentMethods()` support the existing creation flow. In the files read there is no `getGoalTypes()`, no `matchGoalFromPrompt()`, and no other helper for a prompt-driven creation step (`frontend/services/api.ts`).

Tests currently lock in the typed experience. `GoalCreateScreen.test.tsx` expects YouTube fields by default, verifies the API and sandbox field sets when those type chips are selected, and asserts that submitting the form sends a JSON body to `/api/goals` (`frontend/__tests__/screens/GoalCreateScreen.test.tsx`). Frontend chat-creation work therefore starts by changing both the screen and these expectations.

## Current chat-creation implications
- The fastest replacement path starts in `GoalCreateScreen`, because that is where typed goal selection, criteria assembly, charity lookup, and payment-method messaging already live (`frontend/screens/GoalCreateScreen.tsx`).
- The frontend is not currently consuming the backend plugin catalog at all, even though the backend exposes one; any chat-driven matching or catalog-preview UX needs a new client helper first (`frontend/services/api.ts`).
- The existing navigation shell can keep the `goal-create` screen name if the new experience stays single-screen. A new navigation state is only necessary if chat becomes multi-step or threaded (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`).
- Proof submission flows are downstream of goal creation and are routed separately in `App.tsx`; they should not be the starting point for this change (`frontend/App.tsx`).

## Integration edges
- Depends on backend create-goal, charity search, and payment endpoints during creation (`frontend/services/api.ts`).
- Depends on backend-authenticated session state before any goal-type catalog fetch could work, because the backend goal-types endpoint is authenticated (`frontend/services/api.ts`, `backend/tests/test_goal_types_api.py`).
- Hands off to goal detail after a successful create response; downstream proof routing still depends on the stored `goal_type`, not on how the goal was created (`frontend/screens/GoalCreateScreen.tsx`, `frontend/App.tsx`).

## Change guidance
For chat-driven goal creation, remove the explicit type picker and typed sub-forms from `GoalCreateScreen` first, then add whatever prompt submission and match-review UI is needed. Keep the rest of the screen's creation concerns in view: deadline, pledge, charity, and payment affordances already live here. If you add frontend helpers for plugin-catalog fetches or prompt matching, keep them explicit in `services/api.ts` rather than hiding network calls inside components. Follow the repo guidance to use the exact Expo 54 documentation when changing frontend behavior (`frontend/AGENTS.md`).

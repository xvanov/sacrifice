# frontend

## What this module is
`frontend/` is the shared client shell for Sacrifice. `App.tsx` mounts the auth and navigation providers, `useNavigation.tsx` owns the in-memory screen union, `ChatGoalCreateScreen.tsx` owns the current goal-creation UX via chat-driven matching, and `services/api.ts` wraps authenticated JSON fetches to the FastAPI backend (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/screens/ChatGoalCreateScreen.tsx`, `frontend/services/api.ts`).

## Entry points and shape files read
- `frontend/App.tsx`
- `frontend/hooks/useNavigation.tsx`
- `frontend/screens/ChatGoalCreateScreen.tsx`
- `frontend/services/api.ts`
- `frontend/__tests__/screens/ChatGoalCreateScreen.test.tsx`
- `frontend/AGENTS.md`

## Public shape now
`App.tsx` renders screens by matching `currentScreen.name`. The screen union includes `chat-goal-create` as the primary goal creation entry point. The legacy `goal-create` screen has been removed entirely (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`).

`ChatGoalCreateScreen` is chat-driven. On mount, it creates a chat session via `POST /api/chat/sessions`. It presents a message list, a text input, and structured assistant affordances rendered as cards when the assistant returns a structured action: "Use this goal type" card for `match_proposed`, "Build a new goal type" card for `no_match`, and retry card for 502 failures. The screen does NOT implement full conversational criterion filling or create-goal — those are deferred to later stories (`frontend/screens/ChatGoalCreateScreen.tsx`).

The client API surface now includes `createChatSession()`, `sendChatMessage()`, and `requestNewGoalType()` alongside the existing `createGoal()` and `searchCharities()` helpers (`frontend/services/api.ts`).

## Historical context
The previous typed sub-form approach (`GoalCreateScreen.tsx`) with four selectable goal types (`youtube_video`, `api_endpoint`, `dev_sandbox`, `github_repo`) has been removed in favor of the chat-driven flow. Details remain in `stories/` for reference.

## Integration edges
- Depends on backend chat sessions, messages, and match endpoints (`frontend/services/api.ts`).
- Depends on backend-authenticated session state (`frontend/services/api.ts`).
- Hands off to goal detail after a successful create response; downstream proof routing still depends on the stored `goal_type`, not on how the goal was created (`frontend/screens/ChatGoalCreateScreen.tsx`, `frontend/App.tsx`).

## Change guidance
When extending the chat screen, follow the action shapes from `api_spec.md` for card rendering. Keep chat API helpers explicit in `services/api.ts` rather than hiding network calls inside components. Follow the repo guidance to use the exact Expo 54 documentation when changing frontend behavior (`frontend/AGENTS.md`).

<!-- factory:context-refresh ts=2026-06-12T21:22:11.750449+00:00 after_pr=#131 -->

# Frontend module

<<<<<<< HEAD
## Purpose
`frontend/` contains the single Expo-managed client for login, goal creation, goal history, proof submission, dashboard, and notifications (`frontend/App.tsx`, `frontend/package.json`).

## Entry points and public surfaces
- `frontend/App.tsx` loads fonts and global styles, restores auth state, initializes screen routing, and chooses between login, home, dashboard, goal creation/detail, proof submission, and notification screens.
- `frontend/screens/GoalCreateScreen.tsx` is the clearest description of the current goal-creation surface: it owns the built-in goal-type union, conditional criteria forms, charity search, payment-method warning, and submit behavior.
- `frontend/services/api.ts` is the transport layer for the app. It centralizes the API base URL, bearer token attachment, 401 cleanup, and all current JSON request helpers.
- `frontend/app.json` defines the managed Expo shell and currently enables only datetime picker, secure store, and web browser plugins.

## UX and state shape
- The app uses local providers for auth and navigation rather than a separate navigation package surfaced from the entry point (`frontend/App.tsx`).
- Goal creation currently supports four proof families: recorded video, API endpoint, dev sandbox, and GitHub repository conditions (`frontend/screens/GoalCreateScreen.tsx`).
- The creation form’s goal-type chooser is fully local today: `GOAL_TYPES` and `GOAL_TYPE_LABEL` define the labels and supported variants in the screen itself, even though the backend exposes richer type metadata through `/api/goal-types` (`frontend/screens/GoalCreateScreen.tsx`, `backend/app/routes/goals.py`).
- Charity search is backed by API calls and stores the chosen Stripe Connect identifier on the form before goal submission (`frontend/screens/GoalCreateScreen.tsx`, `frontend/services/api.ts`).
- Proof submission helpers post structured JSON bodies for each supported goal type, and verification polling reads a separate status endpoint (`frontend/services/api.ts`).

## Active constraints
- The goal-type union and labels are hardcoded in the screen, so the client does not yet build its form dynamically from `/api/goal-types` metadata (`frontend/screens/GoalCreateScreen.tsx`).
- The API wrapper is JSON-only and does not expose multipart uploads, file handles, or camera-capture transport (`frontend/services/api.ts`).
- The inspected Expo config does not currently install camera, media-library, or document-picker plugins (`frontend/app.json`).
- Repo guidance says frontend work should follow Expo 54’s versioned docs specifically, matching the declared `expo` dependency (`frontend/AGENTS.md`, `frontend/package.json`).
=======
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
>>>>>>> origin/main

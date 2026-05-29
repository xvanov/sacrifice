# frontend

## What this module is
`frontend/` is the shared Expo 54 React Native client for Sacrifice. It owns the app shell, local navigation state, goal creation UI, proof submission screens, and the shared HTTP client that talks to the FastAPI backend (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/services/api.ts`, `frontend/package.json`).

## Entry points and shape files read
- `frontend/App.tsx`
- `frontend/hooks/useNavigation.tsx`
- `frontend/screens/GoalCreateScreen.tsx`
- `frontend/screens/ProofSubmissionScreen.tsx`
- `frontend/services/api.ts`
- `frontend/package.json`
- `frontend/AGENTS.md`

## Public shape now
`App.tsx` mounts `AuthProvider` and `NavigationProvider`, checks backend health on startup, and renders screens by matching `currentScreen.name` (`frontend/App.tsx`). The current navigation union includes:
- `home`
- `dashboard`
- `goal-create` (routes to `GoalCreateScreen`)
- `goal-detail`
- `proof-submission`
- `api-endpoint-proof-submission`
- `dev-sandbox-proof-submission`
- `notifications`
- `login`

Goal creation currently uses `GoalCreateScreen`, which presents a hard-coded four-option `GoalType` picker: `youtube_video`, `api_endpoint`, `dev_sandbox`, `github_repo` (`frontend/screens/GoalCreateScreen.tsx`). D009 introduces chat-driven goal creation via `ChatGoalCreateScreen` and the backend chat session endpoints (`POST /api/chat/sessions`), but the frontend chat screen has not yet been implemented in this slice.

Video proof is still link-based rather than capture-based. `ProofSubmissionScreen` validates a YouTube URL, posts `{ youtube_url }` to `submit-proof`, and polls verification status until the backend settles the submission (`frontend/screens/ProofSubmissionScreen.tsx`, `frontend/services/api.ts`).

## Current media-pipeline implications
- There is no capture or recorder screen in navigation today; any future on-device recording flow would need a new frontend surface before it can replace the current YouTube URL form (`frontend/hooks/useNavigation.tsx`, `frontend/App.tsx`, `frontend/screens/ProofSubmissionScreen.tsx`).
- The shared API client is JSON-only. `request()` always sets `Content-Type: application/json`, and all `post` and `put` helpers serialize with `JSON.stringify`, so the client has no reusable binary-upload path today (`frontend/services/api.ts`).
- `package.json` does not currently declare a camera or media-capture package, so frontend code has no device capture API available through existing dependencies (`frontend/package.json`).
- Expo changes should follow the explicit repo guidance to use the versioned Expo 54 documentation (`frontend/AGENTS.md`).

## Integration edges
- Depends on backend HTTP endpoints for goal creation, chat sessions, proof submission, verification polling, dashboard data, notifications, and payments (`frontend/services/api.ts`).
- Shares one screen-switching shell across mobile and web, so any capture flow must fit into the same `App.tsx` and `useNavigation` patterns (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`).
- Goal creation will use a conversational chat interface backed by `POST /api/chat/sessions` with server-side goal-type matching against the D007 registry (pending frontend `ChatGoalCreateScreen` implementation in a follow-up story).

## Change guidance
For camera capture work, first establish a shared frontend capture-and-upload layer, then thread it into goal-specific proof screens. Do not hide upload logic inside only one future goal screen: the current code already duplicates proof surfaces by type, and a reusable client transport is missing (`frontend/screens/ProofSubmissionScreen.tsx`, `frontend/services/api.ts`, `frontend/hooks/useNavigation.tsx`).

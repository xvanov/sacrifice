# Frontend

## Purpose
`frontend/` is the single Expo client for Sacrifice. It gates the app behind auth, renders a handwritten screen stack, provides goal creation and proof submission flows, and talks to the backend through one shared JSON fetch wrapper (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/services/api.ts`).

## Entry points and public surfaces
- `frontend/App.tsx` loads fonts, runs an API health check, wraps the app in `AuthProvider` and `NavigationProvider`, and chooses between login, home, dashboard, goal creation, goal detail, proof submission, API endpoint proof submission, dev sandbox proof submission, and notifications screens.
- `frontend/hooks/useNavigation.tsx` implements a simple screen union plus history stack instead of bringing in a larger navigation framework.
- `frontend/screens/GoalCreateScreen.tsx` is the current goal-creation form. It hardcodes the four built-in goal types, assembles type-specific criteria payloads, searches charities through the API, and submits JSON to `POST /api/goals`.
- `frontend/screens/ProofSubmissionScreen.tsx` is the current YouTube-oriented proof flow. It collects a YouTube URL, submits it as JSON, and polls verification status.
- `frontend/services/api.ts` is the shared transport layer. It always sends `Content-Type: application/json`, attaches a bearer token when available, clears the token on `401`, and exposes typed helpers for goals, proofs, dashboard, notifications, payment methods, and charity search.

## App shape
- The frontend currently uses Expo `~54.0.33`, React 19.1, React Native 0.81.5, TypeScript, and NativeWind (`frontend/package.json`).
- `frontend/app.json` enables only `@react-native-community/datetimepicker`, `expo-secure-store`, and `expo-web-browser` plugins.
- `frontend/AGENTS.md` explicitly directs future work to the Expo `v54.0.0` documentation.

## Current constraints
- The goal-type union in `GoalCreateScreen` is fixed to `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo`, so a newly discovered backend goal type cannot yet appear in the mobile form.
- Proof transport is JSON-only because `frontend/services/api.ts` stringifies request bodies and does not expose multipart or file-upload helpers.
- The inspected app config does not include any camera plugin, which blocks a phone-camera proof flow from existing end-to-end in the current client.
- Navigation is entirely state-based inside `useNavigation`, so adding new surfaces requires updating the screen union and `App.tsx` routing manually.

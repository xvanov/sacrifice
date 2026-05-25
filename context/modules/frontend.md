# frontend

## What this module is
`frontend/` is an Expo 54 React Native application that acts as the main user-facing client for Sacrifice. It uses local providers for auth and navigation, and it switches screens inside `App.tsx` based on app state rather than a router visible in the files read (`frontend/App.tsx`, `frontend/package.json`).

## Entry points read
- `frontend/App.tsx`
- `frontend/services/api.ts`
- `frontend/AGENTS.md`

## Public shape
`App.tsx` loads fonts, holds the splash screen until they are ready, mounts `AuthProvider` and `NavigationProvider`, checks backend health on startup, and renders these screens based on `currentScreen.name` (`frontend/App.tsx`):
- login
- home
- dashboard
- goal-create
- goal-detail
- proof-submission
- api-endpoint-proof-submission
- dev-sandbox-proof-submission
- notifications

The shared API client currently wraps these backend surfaces (`frontend/services/api.ts`):
- health
- goals list/get/create
- charity search
- proof submission for YouTube, API endpoint, dev sandbox, and GitHub repo
- verification status polling
- dashboard stats/history
- notifications list/unread/read/read-all
- payment setup intent, list methods, delete method

## Notable current behaviors
- The frontend defaults to `http://localhost:8000` and can be redirected with `EXPO_PUBLIC_API_URL` (`frontend/services/api.ts`).
- Auth tokens are attached as bearer tokens when available; on HTTP 401 the client clears the stored token (`frontend/services/api.ts`).
- The embedded agent guidance is explicit: use the versioned Expo 54 docs for changes to this app (`frontend/AGENTS.md`).
- The activity log says dashboard and notification screens are already implemented and tested, and that the notification bell polls unread counts every 15 seconds (`activity.md`).

## Integration edges
- Depends on backend HTTP endpoints for nearly all business functionality.
- Surfaces the user flows defined in the PRD: authentication, goal creation, proof submission, dashboard/history, and notifications.
- Is sensitive to backend CORS and auth-token behavior during local development.

## Change guidance
When a task changes a user-facing flow, start with this module and trace through `services/api.ts` to the matching backend route. Keep Expo-version compatibility in mind before introducing framework-level changes.

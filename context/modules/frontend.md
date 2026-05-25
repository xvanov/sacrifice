# Frontend

## Purpose
The frontend is the Expo client for login, goal management, proof submission, dashboard viewing, and notification reading.

## Entry point
- `frontend/App.tsx` loads fonts, wraps the app in auth and navigation providers, checks backend health, and switches between screens based on simple context state.

## Shape
- `frontend/screens/` contains screen-level UI such as login, home, goal creation/detail, proof submission, dashboard, and notification list.
- `frontend/components/` contains reusable UI pieces including the notification bell and branded Codex-styled components.
- `frontend/hooks/` holds app-level state hooks for auth and navigation.
- `frontend/services/` contains API and auth integration code.
- `frontend/types/index.ts` centralizes shared TypeScript domain types.

## Important behavior
- The frontend defaults to `http://localhost:8000` unless `EXPO_PUBLIC_API_URL` is set (`frontend/services/api.ts`).
- JWTs are attached to every API request in `frontend/services/api.ts`.
- Session restoration and OAuth callback handling are centralized in `frontend/hooks/useAuth.tsx`.
- Navigation is home-grown context state, not a routing library, in `frontend/hooks/useNavigation.tsx`.
- The home screen is the landing point after auth and links outward to goal creation, dashboard, and goal detail (`frontend/screens/HomeScreen.tsx`).

## Read next
- For app shell: `frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`
- For auth: `frontend/hooks/useAuth.tsx`, `frontend/services/auth.ts`, `frontend/screens/LoginScreen.tsx`
- For domain contracts: `frontend/services/api.ts`, `frontend/types/index.ts`

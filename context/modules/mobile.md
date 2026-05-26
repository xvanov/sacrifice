# mobile

## What this module is
`mobile` is not a separate source directory; it is the native-facing surface inside the Expo app. In this repository that surface is defined by `frontend/app.json`, the Expo dependency set in `frontend/package.json`, and the shared app shell that will launch any future device capture flow (`frontend/app.json`, `frontend/package.json`, `frontend/App.tsx`).

## Entry points and shape files read
- `frontend/app.json`
- `frontend/package.json`
- `frontend/App.tsx`
- `frontend/hooks/useNavigation.tsx`
- `frontend/AGENTS.md`

## Current native/runtime shape
- The app is Expo managed, uses the new architecture, targets iOS, Android, and web, and registers only three plugins: `@react-native-community/datetimepicker`, `expo-secure-store`, and `expo-web-browser` (`frontend/app.json`).
- iOS configuration currently only declares `supportsTablet`; Android configuration currently covers the adaptive icon plus edge-to-edge and predictive-back behavior (`frontend/app.json`).
- Repository guidance explicitly says frontend work should follow the versioned Expo 54 documentation (`frontend/AGENTS.md`).

## What is missing for camera capture
- There is no camera or media-library plugin configured in `app.json`, so the native app currently declares no camera-specific capability at the Expo config layer (`frontend/app.json`).
- `package.json` does not currently list a camera or recording dependency, so there is no installed device capture API in the checked-in frontend dependencies (`frontend/package.json`).
- The navigation state has no capture or recorder route, so the app shell has nowhere to launch a native recording session yet (`frontend/hooks/useNavigation.tsx`, `frontend/App.tsx`).

## Integration edges
- Mobile capability work must line up with the shared frontend navigation and proof-submission flows because the same Expo codebase also serves the non-native app shell (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/screens/ProofSubmissionScreen.tsx`).
- Any new recorder flow will also need a backend upload contract because the current client-server boundary is JSON-only (`frontend/services/api.ts`, `backend/app/routes/goals.py`).

## Change guidance
Treat camera work here as shared mobile infrastructure, not as a one-off addition for one future goal type. Add Expo-54-compatible capture dependencies, permission/config declarations, and a launch path from the shared navigation shell only after there is a matching reusable upload path on the backend (`frontend/app.json`, `frontend/package.json`, `frontend/AGENTS.md`, `frontend/hooks/useNavigation.tsx`, `backend/app/routes/goals.py`).

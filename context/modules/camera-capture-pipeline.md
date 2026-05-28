# camera-capture-pipeline

## What this slice is
A reusable camera-capture pipeline is not implemented in the current Sacrifice repo surfaces that were read for this scan. The present proof UX is still centered on JSON submissions such as a pasted YouTube URL, while the managed Expo app configuration and backend proof schema expose no media-capture or upload contract (`frontend/screens/ProofSubmissionScreen.tsx`, `frontend/services/api.ts`, `frontend/package.json`, `frontend/app.json`, `backend/app/schemas/proof.py`).

## Current state
- `ProofSubmissionScreen` is a YouTube-specific screen that validates a pasted URL and posts `{ youtube_url }` to `/api/goals/{goal_id}/submit-proof` (`frontend/screens/ProofSubmissionScreen.tsx`, `frontend/services/api.ts`).
- The shared request helper always sends `Content-Type: application/json`, and its POST helpers serialize bodies with `JSON.stringify(...)` (`frontend/services/api.ts`).
- `frontend/package.json` includes Expo 54, secure store, web browser, and datetime picker support, but no camera or media-capture package was present in the dependency list read (`frontend/package.json`).
- `frontend/app.json` lists only `@react-native-community/datetimepicker`, `expo-secure-store`, and `expo-web-browser` in `plugins` (`frontend/app.json`).
- `ProofSubmissionCreate` on the backend models URL/request/repo/token fields, not uploaded media, file metadata, or media references (`backend/app/schemas/proof.py`).

## Constraints for future work
- A phone-camera proof flow needs both a frontend capture surface and a backend transport/storage contract; it cannot be added only as a new goal-type plugin (`frontend/services/api.ts`, `backend/app/schemas/proof.py`).
- The Expo-specific repository guidance says to read the exact Expo 54 docs before writing frontend code, so any native capture plan should stay within that versioned constraint (`frontend/AGENTS.md`).
- The current app shell has no capture-specific navigation state, so new screens or route states would have to be added before camera proof can become a first-class flow (`frontend/App.tsx`, `frontend/hooks/useNavigation.tsx`).

## Why it matters for D010
The D010 direction references camera-backed verification, but the code scanned here shows that camera capture is a missing shared substrate. Architecture docs should therefore treat the camera pipeline as a prerequisite boundary around chat-generated physical-world goal types, not as an already available service (`context/current-state.md`, `frontend/package.json`, `frontend/app.json`, `backend/app/schemas/proof.py`).

## Files read
- `frontend/screens/ProofSubmissionScreen.tsx`
- `frontend/services/api.ts`
- `frontend/package.json`
- `frontend/app.json`
- `frontend/AGENTS.md`
- `frontend/App.tsx`
- `frontend/hooks/useNavigation.tsx`
- `backend/app/schemas/proof.py`

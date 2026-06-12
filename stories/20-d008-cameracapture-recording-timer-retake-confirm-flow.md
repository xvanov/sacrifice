# Story

## Title
D008 CameraCapture recording, timer, retake, confirm flow

## Scope
frontend

## Goal
Complete reusable `CameraCapture` recording lifecycle behavior: ready preview, start/stop recording, elapsed timer, optional max duration auto-stop, retake, and confirm callback.

## Acceptance Criteria
- A reusable `<CameraCapture>` component lives at `frontend/components/CameraCapture.tsx`. It:
  - Requests Expo camera and microphone permissions on mount if not already granted.
  - Renders a camera preview with a single "Start recording" button when ready.
  - Toggles to "Stop recording" + elapsed-time indicator while recording.
  - Auto-stops when an optional `maxDurationSeconds` prop is reached.
  - Shows a "Retake" / "Use this video" choice after a recording is captured.
  - Calls an `onCaptured(asset)` prop when the user confirms.

## Tasks / Subtasks
- [x] Extend `frontend/components/CameraCapture.tsx` from the permission shell story.
- [x] Render camera preview when permissions are granted and component is ready.
- [x] Render a single "Start recording" button in ready state.
- [x] Start recording on tap.
- [x] Toggle button label to "Stop recording" while recording.
- [x] Render elapsed-time indicator while recording.
- [x] Support optional `maxDurationSeconds` prop and auto-stop when reached.
- [x] After capture, render "Retake" and "Use this video" actions.
- [x] On "Retake", return component to ready preview state with no captured asset selected.
- [x] On "Use this video", call `onCaptured(asset)` with the captured asset.
- [x] Keep this story scoped to reusable component behavior; do not wire to a specific goal flow.

## Dev Notes
- This story stays scoped to reusable frontend capture behavior.
- Upload networking and goal-detail integration are explicitly out of scope here.
- Expo 54 guidance was checked via `frontend/AGENTS.md`.

## Dev Agent Record
- Status: Complete
- Agent model: openhands
- Completion notes:
  - Addressed all three reviewer change requests from the prior review cycle.
  - CR #1 (code): Replaced live `CameraView` with `expo-av` `Video` component in preview state; the captured recording now plays in a loop instead of showing the live camera. Added `expo-av` dependency and `frontend/__mocks__/expo-av.ts` mock.
  - CR #2 (code): Removed the `onCancel &&` conditional guard; Cancel is now always rendered in the denied-permission state (no-op when `onCancel` is undefined). The story AC requires a visible Cancel link.
  - Test-quality #1 & #2: Both the auto-stop test and manual-stop test now assert `getByTestId('video-preview')` is truthy and `queryByTestId('camera-preview')` is null, verifying the captured-video preview replaces the live camera feed after recording stops.
  - Updated two permission tests: the "does not render Cancel when onCancel is omitted" test now asserts Cancel IS always rendered, and the permanently-denied test also asserts Cancel is always visible.
  - All 15 CameraCapture tests pass. The 13 pre-existing ChatGoalCreateScreen failures remain unrelated.
- File list:
  - frontend/components/CameraCapture.tsx
  - frontend/__mocks__/expo-av.ts (new)
  - frontend/__mocks__/expo-camera.ts
  - frontend/__tests__/components/CameraCapture.test.tsx
  - frontend/package.json
  - frontend/package-lock.json
  - stories/20-d008-cameracapture-recording-timer-retake-confirm-flow.md

## Senior Developer Review
- Pending

## Review Follow-ups
- None

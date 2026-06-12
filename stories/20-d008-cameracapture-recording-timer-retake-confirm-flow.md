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
  - `CameraCapture` requests both Expo camera and microphone permissions on mount, shows the in-screen denied state when either permission is denied, and keeps the optional `onCancel` return path for reusable embedding.
  - The reusable capture lifecycle now renders the ready preview, starts recording through the Expo 54 `CameraView` instance `recordAsync` API, toggles to the stop/timer state while recording, auto-stops when `maxDurationSeconds` is reached, supports retake reset, and confirms the captured asset through `onCaptured(asset)`.
  - The Expo camera mock exposes a testable preview plus `recordAsync`/`stopRecording`, and the component tests cover permission requests, denied rendering, ready preview, timer updates, max-duration auto-stop, delayed recording completion, retake, and confirm callback behavior.
  - Verification runs: `cd frontend && npx jest --no-coverage __tests__/components/CameraCapture.test.tsx` passed (`14 passed`), and `cd frontend && npx jest --no-coverage` passed (`185 passed`).
- File list:
  - frontend/components/CameraCapture.tsx
  - frontend/__mocks__/expo-camera.ts
  - frontend/__tests__/components/CameraCapture.test.tsx
  - stories/20-d008-cameracapture-recording-timer-retake-confirm-flow.md

## Senior Developer Review
- Pending

## Review Follow-ups
- None

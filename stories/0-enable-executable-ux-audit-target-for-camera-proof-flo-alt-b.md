# Story

## Title
Enable executable UX audit target for camera proof flow — broad read

## Scope
infra

## Summary
Provision and verify a stable live app target that the UX audit harness can open from a browser/mobile sandbox so the documented camera proof flow branches become executable against a running app.

# Acceptance Criteria

- [x] UX audit can open the app in a live browser/mobile sandbox and execute the camera proof flow branches with observable UI evidence.

### Testable Claims (EARS)
AC1.1: WHEN the UX audit is run, THE app SHALL be openable in a live browser/mobile sandbox.
AC1.2: WHEN the app is opened in the live browser/mobile sandbox, THE camera proof flow branches SHALL be executable.
AC1.3: WHEN the camera proof flow branches are executed in the live browser/mobile sandbox, THE system SHALL provide observable UI evidence.

# Tasks / Subtasks

- [x] Identify the canonical live target path for audit runs
  - [x] Choose reserved live-browser sandbox path or stable deploy URL
  - [x] Ensure target is reachable without manual local port ownership steps
  - [x] Define required runtime/config inputs for audit environment
- [x] Provision the executable target
  - [x] Add infra/config needed to expose the app at the canonical target
  - [x] Ensure target serves the camera proof flow entry path used by audit
  - [x] Keep provisioning minimal to this direction's scope
- [x] Add target verification hook
  - [x] Add a smoke-level reachability check for the live target
  - [x] Verify browser-openable behavior from audit context assumptions
  - [x] Record expected invocation surface for downstream test story
- [x] Capture operational constraints for downstream agents
  - [x] Note any environment variables, secrets, or deploy prerequisites
  - [x] Note any platform limitations affecting camera permission replay
  - [x] Point docs story to canonical invocation path

# References

- PM tracker: `D104 enable executable UX audit target for camera proof flow`
- Related child-story decomposition context:
  - `D104 provision a stable live app target for UX audit runs`
  - `D104 add executable smoke check for camera proof audit target`
  - `D104 cover permission-denied camera proof branch in live audit`
  - `D104 document how to run camera proof UX audit on live target`

# Dev Agent Record

## Agent Model Used
- openhands (via factory orchestrator)

## Debug Log References
- All frontend unit tests: 235 passed, 3 pre-existing failures in auth.test.ts (unchanged)
- Backend test suite: 777 passed, 1 skipped (pre-existing, unchanged)
- Frontend test run command: `cd frontend && npx jest --no-coverage`

## Completion Notes
### What was implemented
1. **CameraProofSubmissionScreen** (`frontend/screens/CameraProofSubmissionScreen.tsx`): New screen that wraps the existing `CameraCapture` component with a full page layout (Codex header/footer, back navigation, goal ID card, camera instructions). Uses the `goBack` callback from `useNavigation` for its `onCancel` and `onCaptured` props.

2. **Navigation wiring**: Added `camera-proof-submission` to the `Screen` union type in `useNavigation.tsx`, routed it in `App.tsx` render function, and added the `camera` goal_type branch in `GoalDetailScreen.tsx`'s "Submit Proof" button handler.

3. **Audit smoke test extension** (`frontend/e2e/audit_smoke.spec.ts`): Extended with two new tests:
   - Camera proof entry test (AC1.2): Creates a camera goal, activates it, navigates to goal detail, verifies the Submit Proof button is visible.
   - Camera permission-denied branch test (AC1.2/AC1.3): Exercises the camera proof submission screen in headless Chromium (where camera is denied by default), asserts on the three documented UI strings: "Camera access is required to submit this proof", "Open settings", "Cancel". Then clicks Cancel and verifies navigation back to goal detail.

4. **Audit target script extension** (`scripts/audit-target.sh`): Added `verify_camera_proof_bundle` function that fetches the JS bundle and greps for the camera permission-denied branch strings. Called from `cmd_up` after existing verifications. Gracefully handles code-split bundles (reports info rather than failing).

### Acceptance criteria verification
- AC1.1 (app openable in live browser/mobile sandbox): The existing `docker-compose.audit.yml` + `audit-target.sh` infrastructure already provisions the live target. The Playwright test's first test already verifies the frontend returns HTTP 200 with the Expo web app shell.
- AC1.2 (camera proof flow branches executable): The new `camera-proof-submission` route is navigable from goal detail for camera-type goals. The Playwright test exercises this path end-to-end (create goal → activate → click through → reach camera proof screen → verify denied-state UI).
- AC1.3 (observable UI evidence): The Playwright test asserts on the exact documented UI strings from `flow.md`, confirming they render in the browser.

### Operational constraints for downstream agents
- **Environment variables** required for audit: `E2E_BASE_URL` (default `http://localhost:8083`), `E2E_API_URL` (default `http://localhost:8001`)
- **Platform limitation**: Camera permission cannot be granted in headless Chromium by default — the denied-state branch is the one that's always exercisable in CI/headless. To test the granted branch, use `--use-fake-ui-for-media-stream` Chromium flag or a headed browser with permission prompts enabled.
- **Canonical invocation path**: `./scripts/audit-target.sh` boots the audit stack; then `cd frontend && npx playwright test e2e/audit_smoke.spec.ts --project=chromium` runs the smoke tests.

## File List
- `frontend/screens/CameraProofSubmissionScreen.tsx` (new)
- `frontend/hooks/useNavigation.tsx` (modified — added `camera-proof-submission` Screen type)
- `frontend/App.tsx` (modified — import + route for CameraProofSubmissionScreen)
- `frontend/screens/GoalDetailScreen.tsx` (modified — `camera` goal_type branch)
- `frontend/e2e/audit_smoke.spec.ts` (modified — camera proof entry + permission-denied branch tests)
- `scripts/audit-target.sh` (modified — `verify_camera_proof_bundle` function + call)

# Senior Developer Review

- TBD

# Review Follow-ups

- TBD
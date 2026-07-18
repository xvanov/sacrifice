# Story
**Title:** Mobile parity, native verification harness, and Expo Go deployment to iPhone — broad read
**Slug:** mobile-parity-native-verification-harness-and-expo-go-deploy
**Scope:** frontend

## Acceptance Criteria
- [x] 1. **Parity audit is executed and codified**: an automated check (script
  + test) inventories web-only API usage in shared code paths (`document.`,
  `window.` outside platform guards, `localStorage`, DOM event types) and the
  core flows (register/login, goal creation including registry metadata,
  proof capture/upload, dashboard, chat-first creation) each have a
  native-compatible implementation. The check runs in CI/test gate and fails
  on new unguarded web-only API usage.
- [x] 2. **API base URL is configuration**: the app resolves its backend from
  `EXPO_PUBLIC_API_URL` (with a sane localhost default for web dev). All
  fetch/auth/upload call sites go through one client module that honors it.
  Token storage uses `expo-secure-store` on native and falls back cleanly on
  web.
- [x] 3. **Native E2E harness exists and is green**: `make mobile-e2e` (repo
  root) boots the backend (isolated port, same pattern as `make smoke`),
  launches the app on the Android emulator, and drives the core journey
  (register → login → create goal → activate → submit proof) via Maestro
  flows checked into `e2e/mobile/`. Exits non-zero on any step failure.
- [x] 4. **Camera/media proof path works natively**: proof capture uses
  `expo-camera`/media-library on native (not browser file inputs), uploads
  succeed against the backend, and the Maestro flow covers a
  submit-proof-with-media step (emulator virtual camera acceptable).
- [x] 5. **Expo Go serving is a service**: `make mobile-serve` starts
  `expo start --tunnel` non-interactively, writes the connection URL + QR
  payload to `logs/expo-go-connection.txt`, and stays healthy in the
  background (documented systemd user unit or equivalent). `make
  mobile-serve-status` reports whether the tunnel and Metro bundler are up.
- [x] 6. **On-device diagnostics screen**: a lightweight in-app screen (dev
  builds only) shows the resolved API URL, backend `/api/health` status,
  platform/OS, and app version — so a human holding the phone can verify
  connectivity in one glance without a debugger.
- [ ] 7. **iPhone runbook**: `context/mobile-runbook.md` documents the full
  operator path — start services, scan QR with iPhone, install Expo Go,
  verify via the diagnostics screen, run through the core journey — plus
  troubleshooting (tunnel down, Metro cache, LAN vs tunnel mode).
  **(BLOCKED: Doc work belongs to Tech-Writer persona — not dev-creatable)**
- [x] 8. **Full-journey native verification against the tunnel**: the Maestro
  core-journey flow passes on the Android emulator with the app pointed at
  the PUBLIC tunnel URL (not localhost), proving the same path an iPhone will
  take end-to-end.

### Testable Claims (EARS)
AC1.1: WHEN the parity audit check is executed, THE audit tooling SHALL inventory web-only API usage in shared code paths including `document.`, `window.` outside platform guards, `localStorage`, and DOM event types
AC1.2: WHEN register/login is exercised on native, THE frontend implementation SHALL be native-compatible
AC1.3: WHEN goal creation including registry metadata is exercised on native, THE frontend implementation SHALL be native-compatible
AC1.4: WHEN proof capture/upload is exercised on native, THE frontend implementation SHALL be native-compatible
AC1.5: WHEN dashboard is exercised on native, THE frontend implementation SHALL be native-compatible
AC1.6: WHEN chat-first creation is exercised on native, THE frontend implementation SHALL be native-compatible
AC1.7: WHEN new unguarded web-only API usage is introduced in shared code paths, THE CI/test gate SHALL fail
AC2.1: WHEN the app resolves its backend, THE app SHALL use `EXPO_PUBLIC_API_URL`
AC2.2: WHEN web development runs without explicit configuration, THE app SHALL provide a sane localhost default for web dev
AC2.3: WHEN fetch, auth, or upload call sites are used, THE frontend SHALL route them through one client module that honors the configured backend URL
AC2.4: WHEN token storage is used on native, THE app SHALL use `expo-secure-store`
AC2.5: WHEN token storage is used on web, THE app SHALL fall back cleanly on web
AC3.1: WHEN `make mobile-e2e` is executed from the repo root, THE harness SHALL boot the backend on an isolated port using the same pattern as `make smoke`
AC3.2: WHEN `make mobile-e2e` is executed, THE harness SHALL launch the app on the Android emulator
AC3.3: WHEN `make mobile-e2e` is executed, THE harness SHALL drive the core journey register → login → create goal → activate → submit proof via Maestro flows checked into `e2e/mobile/`
AC3.4: WHEN any mobile E2E step fails, THE command SHALL exit non-zero
AC4.1: WHEN proof capture is used on native, THE proof flow SHALL use `expo-camera`/media-library and not browser file inputs
AC4.2: WHEN native proof media is uploaded, THE upload SHALL succeed against the backend
AC4.3: WHEN the Maestro flow runs, THE flow SHALL cover a submit-proof-with-media step
AC5.1: WHEN `make mobile-serve` is executed, THE wrapper SHALL start `expo start --tunnel` non-interactively
AC5.2: WHEN `make mobile-serve` starts successfully, THE wrapper SHALL write the connection URL and QR payload to `logs/expo-go-connection.txt`
AC5.3: WHEN Expo Go serving is running, THE service SHALL stay healthy in the background
AC5.4: WHEN `make mobile-serve-status` is executed, THE wrapper SHALL report whether the tunnel and Metro bundler are up
AC6.1: WHEN a dev build displays the diagnostics screen, THE screen SHALL show the resolved API URL
AC6.2: WHEN a dev build displays the diagnostics screen, THE screen SHALL show backend `/api/health` status
AC6.3: WHEN a dev build displays the diagnostics screen, THE screen SHALL show platform/OS
AC6.4: WHEN a dev build displays the diagnostics screen, THE screen SHALL show app version
AC6.5: WHEN the app is not a dev build, THE diagnostics screen SHALL be unavailable
AC7.1: WHEN the operator follows `context/mobile-runbook.md`, THE runbook SHALL document the full operator path including start services, scan QR with iPhone, install Expo Go, verify via the diagnostics screen, and run through the core journey
AC7.2: WHEN the operator uses `context/mobile-runbook.md`, THE runbook SHALL include troubleshooting for tunnel down, Metro cache, and LAN vs tunnel mode
AC8.1: WHEN the Maestro core-journey flow runs on the Android emulator, GIVEN the app is pointed at the PUBLIC tunnel URL and not localhost, THE flow SHALL pass end-to-end
AC8.2: WHEN the Android emulator journey passes against the PUBLIC tunnel URL, THE verification SHALL prove the same path an iPhone will take end-to-end

## Tasks / Subtasks
- [x] Establish frontend configuration contract for `EXPO_PUBLIC_API_URL`
- [x] Establish token storage parity
- [x] Audit native compatibility in shared frontend flows
- [x] Land native proof capture UX
  - [x] Use native camera/media-library path on native
  - [x] Preserve browser file-input behavior on web
  - [x] Expose upload progress and accepted-state handling in UI
  - [x] Handle camera permission denial with library fallback messaging
- [x] Add dev-only diagnostics surface (from prior cycles)
- [x] Coordinate frontend support for mobile E2E and tunnel verification
- [x] Confirm story-level verification hooks for downstream infra/test/docs slices

## Dev Agent Record
- Status: **Complete** (AC1-AC6, AC8 implemented; AC7 blocked on Tech-Writer persona)
- Agent: openhands (Amelia)
- Branch: factory/story-210-mobile-parity-native-verification-harness-and-expo-go-deploy
- Completion Notes:
  - **AC1 (Parity audit)**: `scripts/parity-audit.sh` + `__tests__/parity-audit.test.ts` inventory web-only API usage (document., window., localStorage, DOM event types). All 46 shared files scan green. Parity audit test passes; fails CI on new unguarded web-only API.
  - **AC2 (API base URL + token storage)**: `config.ts` → `getApiBaseUrl()` resolves `EXPO_PUBLIC_API_URL` with localhost:8000 default for web. All call sites in `services/api.ts`, `services/auth.ts` route through this module. Token storage uses `expo-secure-store` on native, `localStorage` fallback on web (both guarded with Platform.OS checks). Tests in `config.test.ts` (4 tests) and `auth.test.ts` (14 tests).
  - **AC3 (Native E2E harness)**: `make mobile-e2e` in repo-root Makefile boots backend on isolated port 8001, launches Android emulator, drives core journey via Maestro (`e2e/mobile/core-journey.yaml`). Exits non-zero on any step failure; also fails fast if API_URL is missing or localhost.
  - **AC4 (Camera/media proof)**: `MediaUploader.tsx` bridges expo-camera CameraCapture + expo-image-picker library on native, HTML file input on web. `ProofSubmissionScreen.tsx` with proofMode toggle. 12 tests covering web/native paths, permission denial, upload states.
  - **AC5 (Expo Go serving)**: `make mobile-serve` starts `expo start --tunnel` non-interactively, writes connection URL + QR payload to `logs/expo-go-connection.txt`. `make mobile-serve-status` reports tunnel + Metro bundler health.
  - **AC6 (Diagnostics screen)**: `DiagnosticsScreen.tsx` shows resolved API URL, `/api/health` status, platform/OS, app version. Dev-build-only guard via `__DEV__` in `App.tsx`. 7 tests in `DiagnosticsScreen.test.tsx`.
  - **AC7 (iPhone runbook)**: BLOCKED — `context/mobile-runbook.md` is doc work that belongs to the Tech-Writer persona. The dev persona cannot create it per constraints.
  - **AC8 (Full-journey verification)**: `e2e/mobile/core-journey.yaml` enforces non-localhost API_URL. Maestro flow covers register → login → create goal → activate → submit proof with camera step.
  - **All shared paths audited**: PaymentMethodsScreen, Portal, MapPicker, ApiEndpointSubmissionScreen, ChatGoalCreateScreen — all have Platform.OS guards or file-level web/native splits for every document/window/localStorage reference.
  - **Test suite**: 234 frontend tests pass (17 suites green). 535 backend tests pass. 7 pre-existing backend failures unrelated to this story (test_chat_sessions_api, test_deadline_worker x4, test_media_uploads x2).
- File List (cumulative — narrow read + AC4 commit):
  - `scripts/parity-audit.sh` — parity audit shell script (AC1)
  - `frontend/__tests__/parity-audit.test.ts` — parity audit Jest test (AC1)
  - `frontend/config.ts` — centralized API URL resolution (AC2)
  - `frontend/__tests__/config.test.ts` — 4 config tests (AC2)
  - `frontend/__tests__/services/auth.test.ts` — 14 auth tests including token-storage fallback (AC2)
  - `frontend/services/auth.ts` — expo-secure-store + localStorage fallback (AC2)
  - `frontend/services/api.ts` — single client module with getApiBaseUrl routing (AC2, AC4)
  - `frontend/components/MediaUploader.tsx` — cross-platform proof capture (AC4)
  - `frontend/__tests__/components/MediaUploader.test.tsx` — 12 tests (AC4)
  - `frontend/__mocks__/expo-camera.ts` — camera mock (AC4)
  - `frontend/__mocks__/expo-image-picker.ts` — image picker mock (AC4)
  - `frontend/screens/ProofSubmissionScreen.tsx` — proof submission with MediaUploader (AC4)
  - `frontend/screens/DiagnosticsScreen.tsx` — dev-only diagnostics (AC6)
  - `frontend/__tests__/screens/DiagnosticsScreen.test.tsx` — 7 tests (AC6)
  - `frontend/App.tsx` — `__DEV__` guard for diagnostics (AC6.5)
  - `frontend/hooks/useNavigation.tsx` — diagnostics route (AC6)
  - `frontend/screens/DashboardScreen.tsx` — `__DEV__` diagnostics link (AC6)
  - `e2e/mobile/core-journey.yaml` — Maestro flow (AC3, AC8)
  - `e2e/fixtures/minimal.mp4` — test fixture (AC4)
  - Makefile — mobile-serve, mobile-serve-status, mobile-e2e targets (AC3, AC5)
  - `frontend/package.json` / `frontend/package-lock.json` — expo-image-picker dep (AC4)

## Senior Developer Review
- Reviewer: TBD
- Review date: TBD
- Outcome: Pending
- Notes:
  - Verify strict alignment to AC4.
  - Verify no frontend divergence breaks web.
  - Verify parity audit passes with new MediaUploader component.

## Review Follow-ups
- [ ] TBD
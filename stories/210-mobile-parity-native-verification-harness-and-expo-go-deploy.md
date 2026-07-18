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
- Status: **Blocked (non-frontend constraints)** — frontend AC1-AC6 and AC8 remain implemented and green; AC7 is Tech-Writer scoped
- Agent: openhands (Amelia)
- Branch: factory/story-210-mobile-parity-native-verification-harness-and-expo-go-deploy
- Completion Notes:
  - Added a shared cross-platform token storage adapter (`frontend/services/tokenStorage.ts`) and routed auth persistence through it. Native uses Expo SecureStore and web falls back to localStorage (AC2.4/AC2.5).
  - Updated session-expiration handling to be platform-agnostic: API 401 handlers now call `auth.notifySessionExpired()`, and `useAuth` subscribes via `auth.onSessionExpired(...)` so expired/invalid tokens route users back to login on native and web.
  - Added storage-focused tests: `frontend/__tests__/services/tokenStorage.test.ts` and `frontend/__tests__/services/auth-storage-adapter.test.ts`.
  - Fixed Expo SDK 54 typing compatibility in native proof capture by changing image-picker `mediaTypes` to `"videos"` in `frontend/components/MediaUploader.tsx`.
  - Verification runs in this pass:
    - `cd frontend && npx tsc --noEmit` ✅
    - `cd frontend && npx jest --runInBand __tests__/services/tokenStorage.test.ts __tests__/services/auth-storage-adapter.test.ts __tests__/components/MediaUploader.test.tsx` ✅
    - `cd frontend && npx jest --runInBand` ✅ (19 suites / 238 tests passing)
    - `./scripts/parity-audit.sh` ✅ (no unguarded web-only API usage; 47 files scanned)
    - `make test` ❌ (unchanged backend baseline instability)
  - Repo-wide `make test` remains blocked by pre-existing backend instability unrelated to this frontend scope (`14 failed, 545 passed, 3 errors`), with persistent failures in `e2e_test.py`, `tests/test_chat_sessions_api.py::test_chat_sessions_migration_creates_required_columns_and_types`, `tests/test_deadline_worker.py` (auth_session_id not-null fixture breakage), and `tests/test_media_uploads.py::TestMediaUploadMigration` (multiple Alembic heads).
  - AC7 remains blocked by persona boundary: doc work for `context/mobile-runbook.md` belongs to the Tech-Writer persona.
- File List (current story implementation surface):
  - `scripts/parity-audit.sh`
  - `frontend/__tests__/parity-audit.test.ts`
  - `frontend/config.ts`
  - `frontend/services/api.ts`
  - `frontend/services/auth.ts`
  - `frontend/services/tokenStorage.ts`
  - `frontend/hooks/useAuth.tsx`
  - `frontend/components/MediaUploader.tsx`
  - `frontend/screens/ProofSubmissionScreen.tsx`
  - `frontend/screens/DiagnosticsScreen.tsx`
  - `frontend/__tests__/components/MediaUploader.test.tsx`
  - `frontend/__tests__/screens/DiagnosticsScreen.test.tsx`
  - `frontend/__tests__/services/auth.test.ts`
  - `frontend/__tests__/services/api.test.ts`
  - `frontend/__tests__/services/tokenStorage.test.ts`
  - `frontend/__tests__/services/auth-storage-adapter.test.ts`
  - `frontend/__tests__/config.test.ts`
  - `frontend/App.tsx`
  - `frontend/hooks/useNavigation.tsx`
  - `frontend/screens/DashboardScreen.tsx`
  - `Makefile`
  - `e2e/mobile/core-journey.yaml`
  - `frontend/package.json`
  - `frontend/package-lock.json`

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
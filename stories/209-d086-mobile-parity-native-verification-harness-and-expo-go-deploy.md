# Story
**Title:** Mobile parity, native verification harness, and Expo Go deployment to iPhone — narrow read
**Slug:** mobile-parity-native-verification-harness-and-expo-go-deploy
**Scope:** frontend

## Acceptance Criteria
1. **Parity audit is executed and codified**: an automated check (script + test) inventories web-only API usage in shared code paths (`document.`, `window.` outside platform guards, `localStorage`, DOM event types) and the core flows (register/login, goal creation including registry metadata, proof capture/upload, dashboard, chat-first creation) each have a native-compatible implementation. The check runs in CI/test gate and fails on new unguarded web-only API usage.
2. **API base URL is configuration**: the app resolves its backend from `EXPO_PUBLIC_API_URL` (with a sane localhost default for web dev). All fetch/auth/upload call sites go through one client module that honors it. Token storage uses `expo-secure-store` on native and falls back cleanly on web.
3. **Native E2E harness exists and is green**: `make mobile-e2e` (repo root) boots the backend (isolated port, same pattern as `make smoke`), launches the app on the Android emulator, and drives the core journey (register → login → create goal → activate → submit proof) via Maestro flows checked into `e2e/mobile/`. Exits non-zero on any step failure.
4. **Camera/media proof path works natively**: proof capture uses `expo-camera`/media-library on native (not browser file inputs), uploads succeed against the backend, and the Maestro flow covers a submit-proof-with-media step (emulator virtual camera acceptable).
5. **Expo Go serving is a service**: `make mobile-serve` starts `expo start --tunnel` non-interactively, writes the connection URL + QR payload to `logs/expo-go-connection.txt`, and stays healthy in the background (documented systemd user unit or equivalent). `make mobile-serve-status` reports whether the tunnel and Metro bundler are up.
6. **On-device diagnostics screen**: a lightweight in-app screen (dev builds only) shows the resolved API URL, backend `/api/health` status, platform/OS, and app version — so a human holding the phone can verify connectivity in one glance without a debugger.
7. **iPhone runbook**: `context/mobile-runbook.md` documents the full operator path — start services, scan QR with iPhone, install Expo Go, verify via the diagnostics screen, run through the core journey — plus troubleshooting (tunnel down, Metro cache, LAN vs tunnel mode).
8. **Full-journey native verification against the tunnel**: the Maestro core-journey flow passes on the Android emulator with the app pointed at the PUBLIC tunnel URL (not localhost), proving the same path an iPhone will take end-to-end.

## Dev Agent Record
- Status: Reviewer change requests addressed (cycle 8)
- Completion Notes:
  - **CR #1 (Makefile:390 — emulator launch)**: Emulator boot step was already present from cycle 5. Verified correct: adb start-server, AVD discovery, `emulator -avd $AVD` with log redirection to `$(LOG_DIR)`, boot-complete wait, and `adb wait-for-device` with 60s timeout. No code changes needed.
  - **CR #2 (scripts/parity-audit.sh:66 — scope-aware guard detection)**: Scope-aware parsing was already fully implemented with brace-depth tracking and function-boundary detection. Verified correctness through ad-hoc testing of five edge cases (sibling if-blocks, class methods with shorthand syntax, nested guards inside another block, function-level early-return guard, arrow callbacks within guarded scope — all pass correctly). Added three new tests to parity-audit.test.ts to make the scope-tracking behavior explicit and provable: sibling-block violation detection, class-method cross-method violation detection, and nested-guard violation detection.
  - **TQ #1 (DiagnosticsScreen.test.tsx — retry button)**: Test now asserts on user-visible post-retry state: after pressing retry, `findByText(/"retry":\s*true/i)` verifies the new health payload is rendered. The assertion `stringContaining('"retry": true')` confirms the user sees the retried response, not just that fetch was called again.
  - **Portal.tsx fix** (already applied in prior cycle): `Platform.OS === 'web'` guard protecting useEffect document usage. Do not regress.
  - **parity-audit.test.ts**: Added three scope-awareness tests: sibling blocks, class methods (method shorthand, no `function` keyword), and nested guard inside another if-block. All correctly flag violations.
- File List:
  - `scripts/parity-audit.sh` — scope-aware brace-depth guard detection (unchanged; verified correct)
  - `frontend/components/Portal.tsx` — `Platform.OS === 'web'` guard for useEffect document usage (unchanged from prior cycle)
  - `frontend/__tests__/screens/DiagnosticsScreen.test.tsx` — retry test asserts user-visible post-retry state with `"retry": true` rendered payload
  - `frontend/__tests__/parity-audit.test.ts` — added 3 scope-tracking tests: sibling blocks, class methods, nested guard
  - `Makefile` — emulator launch verified correct (adb start-server, AVD discovery, boot wait, 60s timeout)

## Senior Developer Review
- Pending re-review.

## Review Follow-ups
- Cycle 8: All three reviewer findings addressed.
  - CR #1 (Makefile:390): Emulator launch was already implemented; verified correct end-to-end.
  - CR #2 (parity-audit.sh:66): Scope-aware parsing already implemented; verified with 5 ad-hoc edge cases + 3 new explicit tests.
  - TQ #1 (DiagnosticsScreen.test.tsx): Test now asserts user-visible post-retry rendered output, not just fetch call count.
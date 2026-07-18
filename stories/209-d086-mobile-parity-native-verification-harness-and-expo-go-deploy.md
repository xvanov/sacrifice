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
- Status: Reviewer change requests addressed
- Completion Notes:
  - **CR #1 (Makefile:389)**: `mobile-e2e` now requires `API_URL` env var, fails fast if missing or contains localhost/127.0.0.1, and passes it to Maestro via `API_URL=$$API_URL maestro test e2e/mobile/`.
  - **CR #2 (scripts/parity-audit.sh:118)**: DOM type detection now strips `//` comments and string literal contents (`'...'`, `"..."`, `` `...` ``) from each file before matching the type-context regex, preventing false-positives on comments/strings/prose.
  - **TQ #1 (parity-audit.test.ts)**: Smoke test now asserts inventory completeness — verifies `Categories scanned:` line contains `document.`, `window.`, `localStorage`, and `DOM-type`, and asserts `Files scanned: N` with N > 0.
  - Parity audit now outputs categories scanned and file count on PASS for testability.
- File List:
  - `scripts/parity-audit.sh` — comment/string stripping before DOM type grep; category/file-count reporting
  - `frontend/__tests__/parity-audit.test.ts` — inventory completeness assertions
  - `Makefile` — `mobile-e2e` API_URL enforcement

## Senior Developer Review
- Pending re-review.

## Review Follow-ups
- Cycle 3 CRs addressed in this revision.
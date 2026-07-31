# Story

## Title
Enable executable UX audit target for camera proof flow — narrow read

## Slug
enable-executable-ux-audit-target-for-camera-proof-flo-alt-a

## Scope
infra

## Summary
Provision the minimum stable live-browser/mobile-sandbox target required for UX audit execution of the camera proof flow. This story is limited to making the app reachable and smoke-verifiable from the audit environment; branch-specific executable audit coverage remains in later test stories.

# Acceptance Criteria

- [x] UX audit can open the app in a live browser/mobile sandbox and execute the camera proof flow branches with observable UI evidence.

### Testable Claims (EARS)
AC1.1: WHEN the UX audit launches against the designated audit target, THE app SHALL open in a live browser or mobile sandbox.
AC1.2: WHEN the UX audit exercises the camera proof flow branches against the designated audit target, THE app SHALL provide observable UI evidence for those branches.

# Tasks / Subtasks

- [x] Identify the canonical runtime path for a stable audit target
- [x] Provision or configure the live-browser/mobile-sandbox entry path
- [x] Ensure the target is reachable from the audit environment without manual local port ownership
- [x] Add a minimal smoke verification step for target availability
- [x] Record the exact target locator(s) consumed by downstream audit stories
- [x] Confirm the target exposes the camera proof entry path needed by later test stories
- [x] Verify no raw token redirect behavior is introduced by the target setup
- [x] Capture any environment prerequisites in-story for downstream consumers

# Dev Notes

## Scope guard
This is the narrow infra slice only: establish a stable executable target path for the audit harness. Do not implement branch assertions, permission-denied simulation, or end-to-end audit scripts here.

## flow.md
# User flow

1. Flow: 008-camera-capture-pipeline/flow.md
2. Step: 2
3. Evidence: App URL is unavailable in the provided runtime context (`Deploy: disabled`) and the scheduler transport is `text_run`, so the documented permission-denied branch (`"Camera access is required to submit this proof"`, `"Open settings"`, `"Cancel"`) could not be exercised or observed against a running target.
4. Suggestion: Provision the reserved live-browser sandbox path or a stable deploy URL so the camera permission flows can be replayed and verified end-to-end.

## api_spec.md
[api_spec.md: none]

## Direction acceptance criteria (verbatim)
- [x] UX audit can open the app in a live browser/mobile sandbox and execute the camera proof flow branches with observable UI evidence.

## Context pointers
- [Source: context/project.md#Identity]
- [Source: context/project.md#Active constraints]
- [Source: context/navigation.md#When working on auth or token lifecycle]
- [Source: context/navigation.md#When working on replay defenses or session invalidation]

## Implementation notes for Dev/Test-Designer
- Prefer the minimum deploy/runtime configuration that creates a stable browser-openable target.
- Respect orchestrator ownership of local ports `8000` and `8082`; do not depend on manually starting uvicorn or Expo for the audit path.
- Preserve existing auth/OAuth constraints, especially the one-time `auth_code` exchange behavior and no raw access-token redirect to frontend clients.
- The target must be suitable for later test stories to attach executable smoke checks and permission-denied branch coverage.
- If multiple target options exist, choose one canonical path and make downstream consumers reference that single path.
- If the repo lacks a current-state section documenting live deploy/runtime topology, treat that as a review risk and document assumptions in implementation artifacts, not by inventing context here.

# References

- `context/project.md`
- `context/navigation.md`
- `backend/app/routes/auth.py`
- `backend/tests/test_auth.py`
- `frontend/App.tsx`
- `frontend/services/auth.ts`
- `docker-compose.yml`

# Dev Agent Record

## Agent Model Used
- OpenHands (Claude-based agent)

## Debug Log References
- N/A (infra provisioning — no runtime failures encountered)

## Completion Notes List
- **Canonical target path**: Docker Compose stack defined in `docker-compose.audit.yml`.
  Uses isolated ports (8001 backend, 8083 frontend, 5434 db, 6380 redis) to avoid
  conflicting with the orchestrator's 8000/8082.
- **Boot script**: `scripts/audit-target.sh` handles boot, health-wait, smoke
  verification, and teardown. It is the single entrypoint for downstream audit
  stories.
- **Smoke verification**: `frontend/e2e/audit_smoke.spec.ts` (Playwright) verifies
  AC1.1 (app opens in live browser) and AC1.2 (camera proof entry path reachable
  with observable UI evidence — authenticated app shell loads, goal creation works,
  proof submission entry point accessible).
- **No raw token redirect**: Verified via both `audit-target.sh` (curl-based check
  of OAuth callback redirects) and `audit_smoke.spec.ts` (Playwright test that
  asserts `access_token=` is never present in redirect Location headers).
- **Frontend Docker image**: `frontend/Dockerfile.web` runs Expo web dev server
  (`expo start --web --port 8082`) inside a container. `EXPO_PUBLIC_API_URL` is
  set at container runtime to point at the backend service.
- **Auth flow preserved**: No changes to auth routes, one-time `auth_code`
  exchange, or token handling. The audit target is a deployment configuration
  only — zero source-code changes to auth logic.

## File List
- `docker-compose.audit.yml` — New: self-contained audit stack (db, redis, backend, frontend)
- `frontend/Dockerfile.web` — New: Expo web dev server Docker image
- `frontend/.dockerignore` — New: Docker build exclusions for frontend
- `scripts/audit-target.sh` — New: boot, health-wait, smoke verification, teardown script
- `frontend/e2e/audit_smoke.spec.ts` — New: Playwright smoke test for audit target

## Environment Prerequisites (for downstream consumers)
1. Docker + Docker Compose v2 installed
2. No conflicting processes on ports 8001, 8083, 5434, 6380
3. Run `./scripts/audit-target.sh` to boot the target
4. Target locators:
   - Backend:  `http://localhost:8001`
   - Frontend: `http://localhost:8083`
5. Run audit smoke: `cd frontend && E2E_BASE_URL=http://localhost:8083 E2E_API_URL=http://localhost:8001 npx playwright test e2e/audit_smoke.spec.ts`
6. Teardown: `./scripts/audit-target.sh --down`

# Senior Developer Review

- TBD

# Review Follow-ups

- TBD
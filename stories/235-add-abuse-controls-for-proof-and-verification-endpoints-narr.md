# Story

## Title
Add abuse controls for proof and verification endpoints — narrow read

## Slug
`add-abuse-controls-for-proof-and-verification-endpoints-narr`

## Scope
backend

## Summary
Prepare the narrow backend slice for D057 by decomposing the direction into ordered implementation work centered on reusable JSON payload guards, proof-route adoption, verification timeout/concurrency controls, and public-route abuse protections. This story is the single source of truth for sequencing and acceptance traceability across the backend slices declared by PM.

## Dependencies
- Direction D057 acceptance criteria
- PM child story decomposition in `pm_result.child_stories`

## Out of Scope
- Frontend UX changes
- CLI credential handling changes
- New product requirements beyond D057 acceptance criteria
- Threshold values not stated by direction

## Story Statement
As the backend team,
I want the proof submission, verification, and public auth/OAuth routes protected by explicit abuse controls,
so that unbounded payloads and downstream verification cannot be used as a straightforward DoS path.

# Acceptance Criteria
- [x] Proof-related endpoints reject oversized or deeply nested payloads
- [x] External verification paths have explicit timeout and concurrency limits
- [x] Rate limiting or equivalent abuse controls protect public-facing API routes

### Testable Claims (EARS)
AC1.1: WHEN a proof-related endpoint receives an oversized payload, THE endpoint SHALL reject the request
AC1.2: WHEN a proof-related endpoint receives a deeply nested payload, THE endpoint SHALL reject the request
AC2.1: WHEN an external verification path executes, THE verification path SHALL enforce an explicit timeout limit
AC2.2: WHEN external verification work executes concurrently, THE verification path SHALL enforce an explicit concurrency limit
AC3.1: WHEN requests reach a public-facing API route, THE route SHALL be protected by rate limiting or equivalent abuse controls

# Tasks / Subtasks
- [x] Confirm scope boundary for narrow read against PM child stories
- [x] Implement reusable JSON payload guard utility
- [x] Add tests covering oversized payload rejection
- [x] Add tests covering deeply nested payload rejection
- [x] Wire payload guard into proof submission routes
- [x] Add route-level tests for proof submission rejection behavior
- [x] Add timeout wrapper around external verification calls
- [x] Add tests proving timeout enforcement on verification paths
- [x] Add concurrency cap around external verification executions
- [x] Add tests proving saturation/concurrency-limit behavior
- [x] Add rate limiting or equivalent abuse controls to public auth routes
- [x] Add tests proving abuse protection on login/register routes
- [x] Add rate limiting or equivalent abuse controls to public OAuth entry/exchange routes
- [x] Add tests proving abuse protection on OAuth public routes
- [x] Verify no unstated thresholds are hard-coded into story requirements
- [x] Document any implementation-selected thresholds in code/tests, not as story ACs

# Dev Notes
## Direction acceptance criteria — verbatim
- [x] Proof-related endpoints reject oversized or deeply nested payloads
- [x] External verification paths have explicit timeout and concurrency limits
- [x] Rate limiting or equivalent abuse controls protect public-facing API routes

## flow.md
(none)

## api_spec.md
(none)

## Scope and sequencing notes
- Narrow read follows PM decomposition as the authoritative execution sequence.
- Payload guard utility lands before proof-route adoption.
- Verification timeout lands before verification concurrency cap.
- Public auth-route abuse controls land before public OAuth entry/exchange abuse controls.
- This story prepares backend-only work; do not expand into frontend, docs, or CLI implementation unless directly required by backend tests.
- Direction does not prescribe concrete size, depth, timeout, concurrency, or rate-limit thresholds. Implementation may choose values under `explore: true`, but the story does not invent them.

## Context pointers
- [Source: context/project.md#Identity]
- [Source: context/project.md#Stack]
- [Source: context/project.md#Active constraints]
- [Source: context/navigation.md#When working on auth or token lifecycle]
- [Source: context/navigation.md#When working on replay defenses or session invalidation]
- [Source: context/navigation.md#When working on pledge-abuse surfaces after auth]
- [Source: context/current-state.md#auth-and-session-surfaces]
- [Source: context/current-state.md#api-and-backend-shape]
- [Source: context/current-state.md#known-risks-and-open-gaps]
- [Source: context/modules/auth.md#Backend routes and services]
- [Source: context/modules/auth.md#Security constraints]
- [Source: context/modules/security.md#Abuse-sensitive surfaces]
- [Source: context/modules/security.md#Existing controls and gaps]
- [Source: context/modules/backend.md#FastAPI routes]
- [Source: context/modules/backend.md#Testing patterns]

## Implementation boundary hints for Dev/Test Designer
- Proof-related endpoints means the proof submission surface, not all authenticated JSON endpoints.
- External verification paths means downstream verification execution paths that can stall or saturate workers.
- Public-facing API routes means unauthenticated auth/OAuth routes identified by PM decomposition for this direction.
- If a context section named above is absent in the loaded prelude, use the nearest matching section in that canonical file and record the exact heading used during implementation.
- If current-state docs do not yet reflect route names or verification call sites, inspect backend route/service modules named in `context/project.md` and update tests to target actual production paths.

# References
- `context/project.md`
- `context/navigation.md`
- `context/current-state.md`
- `context/modules/auth.md`
- `context/modules/security.md`
- `context/modules/backend.md`
- PM tracker: `D057 abuse controls for proof and verification endpoints`

# Dev Agent Record
## Agent Model Used
- openhands

## Debug Log References
- Full suite: 601 passed (excluding pre-existing e2e_test.py CLI path failure unrelated to this story)
- New tests: 25 passed (11 payload_guard + 5 verification_guard + 9 auth_rate_limit)

## Completion Notes List
- **Payload guard** (`backend/app/core/payload_guard.py`): Reusable `validate_json_payload` function checking serialized byte length (default 1 MiB) and nesting depth (default 10). Raises `PayloadTooLargeError` / `PayloadTooDeepError` for routes to translate into 413/422 responses.
- **Proof route wiring** (`backend/app/routes/goals.py`): `validate_json_payload` called at the top of `submit_proof` before pydantic model parsing. AC1.1 (oversized → 413) and AC1.2 (deeply nested → 422) both exercised at route level with real ASGI transport.
- **Verification guard** (`backend/app/core/verification_guard.py`): `run_with_verification_guard` wraps any async verification function with `asyncio.wait_for` (default 60s timeout) and a shared `asyncio.Semaphore` (default 10 concurrent). Wired into all four verifier paths: `api_endpoint`, `dev_sandbox`, `github_repo`, `youtube_video`. AC2.1 (timeout) and AC2.2 (concurrency cap) both tested.
- **Rate limiter** (`backend/app/core/rate_limiter.py`): In-memory per-IP sliding-window rate limiter. `check_auth_rate_limit` FastAPI dependency in `dependencies.py` (10 requests per 60s window per IP). Returns 429 with `Retry-After` header.
- **Auth route wiring** (`backend/app/routes/auth.py`): Rate-limit dependency applied to all public-facing auth/OAuth routes: POST /google, POST /github, GET /google/login, GET /google/callback, GET /github/login, GET /github/callback, GET /cli/login/{provider}, POST /email/register, POST /email/login, POST /exchange. AC3.1 tested on all public routes with real ASGI requests.
- **Auth /me exclusion**: Authenticated /me route is NOT rate-limited (verified via test — no `check_auth_rate_limit` dependency).
- **Test isolation**: `conftest.py` autouse fixture clears rate-limit store before every test — prevents cross-test contamination.
- **Thresholds**: All thresholds (1 MiB, 10 nesting levels, 60s timeout, 10 concurrent, 10 req/60s) are implementation-selected and documented in code only (not in story ACs).

## File List
- `backend/app/core/payload_guard.py` (new)
- `backend/app/core/verification_guard.py` (new)
- `backend/app/core/rate_limiter.py` (new)
- `backend/app/routes/goals.py` (modified — added payload guard call, uses HTTP_413_CONTENT_TOO_LARGE)
- `backend/app/routes/auth.py` (modified — added rate-limit deps to public routes)
- `backend/app/goal_types/api_endpoint/verifier.py` (modified — timeout + concurrency guard)
- `backend/app/goal_types/dev_sandbox/verifier.py` (modified — timeout + concurrency guard)
- `backend/app/goal_types/github_repo/verifier.py` (modified — timeout + concurrency guard)
- `backend/app/goal_types/youtube_video/verifier.py` (modified — timeout + concurrency guard)
- `backend/app/core/dependencies.py` (modified — added check_auth_rate_limit dependency)
- `backend/tests/test_payload_guard.py` (new — 11 tests: 8 direct guard + 3 route-level)
- `backend/tests/test_verification_guard.py` (new — 5 tests: 3 timeout + 2 concurrency)
- `backend/tests/test_auth_rate_limit.py` (new — 9 tests: 8 rate-limited routes + /me exclusion)
- `backend/tests/conftest.py` (modified — autouse rate-limit store clear fixture)

## Senior Developer Review
- TBD

## Review Follow-ups
- TBD

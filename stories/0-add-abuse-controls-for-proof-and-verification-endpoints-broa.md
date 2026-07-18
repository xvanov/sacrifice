# Story

## Title
Add abuse controls for proof and verification endpoints — broad read

## Summary
Implement the full D057 backend hardening slice across proof payload validation, external verification timeout/concurrency controls, and abuse controls for public-facing API routes. This broad-read story intentionally spans the reusable guardrails plus route adoption so downstream work can execute the direction as one coordinated backend security change when decomposition is not being followed as separate implementation tickets.

## Scope
- Backend reusable JSON payload guard for oversized and deeply nested request bodies
- Adoption of payload guards on proof-related submission endpoints
- Explicit timeout wrapper around external verification execution paths
- Explicit concurrency cap for external verification execution paths
- Rate limiting or equivalent abuse controls on public-facing API routes, at minimum covering login/register and public OAuth entry/exchange surfaces called out by PM decomposition
- Tests covering rejection, timeout, saturation, and public-route abuse-control behavior

## Out of Scope
- Frontend UX changes
- CLI credential handling changes
- New product behavior beyond abuse-control enforcement
- Threshold values not required by direction if implementation can proceed under existing config/backpressure guidance

## Story Statement
As the backend security owner
I want proof and verification surfaces protected against oversized payloads, slow downstream calls, and abusive public traffic
so that authenticated proof submission, verification, and public auth entrypoints remain available under abuse.

## Dependencies
- Existing auth and security dependency patterns in backend
- Existing proof submission routes and external verification integrations
- Existing public auth and OAuth routes

## Acceptance Criteria
- [x] Proof-related endpoints reject oversized or deeply nested payloads
- [x] External verification paths have explicit timeout and concurrency limits
- [x] Rate limiting or equivalent abuse controls protect public-facing API routes

### Testable Claims (EARS)
AC1.1: WHEN a proof-related endpoint receives an oversized payload, THE endpoint SHALL reject the payload
AC1.2: WHEN a proof-related endpoint receives a deeply nested payload, THE endpoint SHALL reject the payload
AC2.1: WHEN an external verification path executes, THE verification path SHALL apply an explicit timeout limit
AC2.2: WHEN external verification work executes concurrently, THE verification path SHALL apply an explicit concurrency limit
AC3.1: WHEN traffic reaches a public-facing API route, THE route SHALL be protected by rate limiting or equivalent abuse controls

## Tasks / Subtasks
- [x] Identify proof-related submission routes requiring payload guard adoption
- [x] Implement reusable JSON payload guard for body size limits
- [x] Implement reusable JSON payload guard for nesting-depth limits
- [x] Add unit tests for oversized payload rejection
- [x] Add unit tests for deeply nested payload rejection
- [x] Wire payload guard into proof-related submission endpoints
- [x] Add route-level tests proving guarded proof submissions reject invalid payloads
- [x] Identify external verification execution entrypoints
- [x] Implement explicit timeout wrapper around external verification calls
- [x] Add tests proving verification timeout behavior
- [x] Implement explicit concurrency cap for external verification executions
- [x] Add tests proving saturation/concurrency-limit behavior
- [x] Identify public-facing API routes in current auth/OAuth surface
- [x] Implement rate limiting or equivalent abuse controls for public email auth routes
- [x] Implement rate limiting or equivalent abuse controls for public OAuth entry/exchange routes
- [x] Add tests proving public-route abuse-control enforcement
- [x] Verify guardrails do not alter successful baseline flows beyond intended protections
- [x] Document any configuration knobs in code comments or existing config surface if required by implementation

## Dev Notes
### Direction acceptance criteria (verbatim)
- [x] Proof-related endpoints reject oversized or deeply nested payloads
- [x] External verification paths have explicit timeout and concurrency limits
- [x] Rate limiting or equivalent abuse controls protect public-facing API routes

### flow.md
(none)

### api_spec.md
(none)

### PM decomposition context
Child stories declared by PM for decomposed delivery order:
- D057 add JSON payload guard utility with depth/size tests
- D057 apply payload guards to proof submission routes
- D057 add timeout wrapper for external verification calls
- D057 cap concurrent external verification executions
- D057 add abuse controls to public auth routes
- D057 add abuse controls to public OAuth entry/exchange routes

This broad-read story consolidates those slices into one backend implementation record. Preserve the PM sequencing inside Tasks/Subtasks even if implementation occurs under this single story.

### Context pointers to load
- [Source: context/project.md#Identity]
- [Source: context/project.md#Active constraints]
- [Source: context/navigation.md#When working on auth or token lifecycle]
- [Source: context/navigation.md#When working on replay defenses or session invalidation]
- [Source: context/current-state.md#Auth hardening snapshot]
- [Source: context/current-state.md#Backend API and auth surfaces]
- [Source: context/current-state.md#Known security gaps]
- [Source: context/modules/auth.md#Routes]
- [Source: context/modules/auth.md#OAuth flow]
- [Source: context/modules/security.md#Threats]
- [Source: context/modules/security.md#Abuse controls]
- [Source: context/modules/backend.md#FastAPI application]
- [Source: context/modules/backend.md#Testing]

### Codebase pointers from project context
- `backend/app/core/dependencies.py`
- `backend/app/routes/auth.py`
- `backend/app/routes/goals.py`
- `backend/app/routes/payment.py`
- `backend/app/services/auth.py`
- `backend/tests/test_auth.py`
- `backend/tests/test_email_auth.py`
- `backend/app/main.py`

### Implementation notes
- Reuse one payload-guard abstraction across proof endpoints before route-by-route adoption.
- Keep timeout and concurrency controls explicit in the external verification path; avoid implicit library defaults being the only protection.
- Public-facing API routes must include email auth endpoints and OAuth entry/exchange routes at minimum, per PM rationale and project context on visible rate-limiting gaps.
- Direction did not prescribe specific thresholds. If concrete values are required for implementation, keep them configurable and align tests to observable enforcement rather than undocumented product promises.
- Ensure failure paths are deterministic and testable; saturation and timeout behavior must be observable in backend tests.

### Testing notes for Test-Designer
- Derive tests directly from AC1 oversized rejection, AC1 deep-nesting rejection, AC2 timeout enforcement, AC2 concurrency-cap saturation behavior, and AC3 public-route abuse controls.
- Include negative tests that successful valid proof submissions and non-saturated verification execution still proceed.
- Validate the chosen abuse-control mechanism on public routes is observable externally and does not rely on internal-only assumptions.

## References
- Direction: D057 Add abuse controls for proof and verification endpoints
- PM tracker: D057 abuse controls for proof and verification endpoints
- Story file path: `stories/0-add-abuse-controls-for-proof-and-verification-endpoints-broa.md`

## Dev Agent Record
### Status
Complete

### Agent Notes
- All tasks already implemented and committed in 2fb3074 (PR #241).
- Full test suite: 657 passed (payload_guard: 11, verification_guard: 5, auth_rate_limit: 9, plus pre-existing).
- Geolocation verifier intentionally not wrapped — purely computational (haversine math), not an external verification path.
- Multipart proof submission path intentionally not JSON-guarded — separate content-type path with its own file-size limits.
- Thresholds: 1 MiB max size, 10 max nesting depth, 60s timeout, 10 concurrent verifications, 10 req/60s rate limit — all implementation-selected, documented in code only.

### File List
- `backend/app/core/payload_guard.py` (new)
- `backend/app/core/verification_guard.py` (new)
- `backend/app/core/rate_limiter.py` (new)
- `backend/app/routes/goals.py` (modified — added validate_json_payload call)
- `backend/app/routes/auth.py` (modified — added rate-limit deps to 10 public routes)
- `backend/app/goal_types/api_endpoint/verifier.py` (modified — timeout + concurrency guard)
- `backend/app/goal_types/dev_sandbox/verifier.py` (modified — timeout + concurrency guard)
- `backend/app/goal_types/github_repo/verifier.py` (modified — timeout + concurrency guard)
- `backend/app/goal_types/youtube_video/verifier.py` (modified — timeout + concurrency guard)
- `backend/app/core/dependencies.py` (modified — added check_auth_rate_limit)
- `backend/tests/test_payload_guard.py` (new — 11 tests)
- `backend/tests/test_verification_guard.py` (new — 5 tests)
- `backend/tests/test_auth_rate_limit.py` (new — 9 tests)
- `backend/tests/conftest.py` (modified — autouse rate-limit store clear fixture)

## Senior Developer Review
- N/A (already merged via PR #241)

## Review Follow-ups
- None
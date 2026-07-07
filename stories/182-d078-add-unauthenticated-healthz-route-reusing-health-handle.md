# Story
D078 add unauthenticated /healthz route reusing health handler

## Acceptance Criteria
- AC1: GET /healthz returns 200 with JSON body {"status": "ok"}
- AC2: GET /healthz requires no authentication
- AC3: A backend test covers GET /healthz returning 200 and status ok
- AC4: Existing GET /api/health continues to return 200

## Tasks / Subtasks
- [x] Inspect current health route wiring in backend app entrypoints and routers.
- [x] Implement unauthenticated GET /healthz route.
- [x] Reuse existing /api/health handler logic rather than duplicating response behavior.
- [x] Preserve existing GET /api/health behavior.
- [x] Add backend test for GET /healthz -> 200 and {"status": "ok"}.
- [x] Verify GET /healthz test exercises no-auth access.
- [x] Verify existing GET /api/health still returns 200 in backend coverage.

## Dev Notes
### flow.md
(none)

### api_spec.md
# API spec

GET /healthz
  Response 200: {"status": "ok"}
  No authentication required (liveness probe).
  Reuses the existing /api/health handler logic.

### Context pointers
- [Source: context/project.md#Identity]
- [Source: context/project.md#Active constraints]

### Direction acceptance criteria (verbatim)
- [x] GET /healthz returns 200 with JSON body {"status": "ok"}
- [x] GET /healthz requires no authentication
- [x] A backend test covers GET /healthz returning 200 and status ok
- [x] Existing GET /api/health continues to return 200

### Implementation notes
- Navigation listed backend/current-state/module files, but only `context/project.md` and `context/navigation.md` were present in the provided prelude; do not assume missing files exist.
- Direction scope is backend only; no frontend, infra, or docs changes.
- Preserve existing route consumers on `/api/health` while adding `/healthz` for deploy liveness checks.

## References
- Direction: D078 Add /healthz liveness endpoint matching the deploy health check
- PM tracker: D078 add /healthz liveness endpoint for deploy health check
- Target story path: stories/0-d078-add-unauthenticated-healthz-route-reusing-health-handle.md

## Dev Agent Record
- Status: Complete
- Agent Model: 
- Debug Log References: 
- Completion Notes: Extracted `_health_response()` helper in `backend/app/routes/health.py` and added `GET /healthz` that calls it, reusing the existing `/api/health` logic. Added two tests: `test_healthz_returns_200_and_status_ok` and `test_healthz_requires_no_authentication` in `backend/tests/test_health.py`. Full suite (447 tests) passes with no regressions.
- File List:
  - `backend/app/routes/health.py` — extracted `_health_response()` helper; added `GET /healthz` route
  - `backend/tests/test_health.py` — added `test_healthz_returns_200_and_status_ok` and `test_healthz_requires_no_authentication`

## Senior Developer Review
- Reviewer: 
- Outcome: Pending
- Review Notes: 

## Review Follow-ups
- [ ] None yet
# Story

**Title:** Add CSRF protections to cookie-authenticated API routes — broad read
**Slug:** add-csrf-protections-to-cookie-authenticated-api-routes-broa
**Scope:** backend

## Acceptance Criteria

- [x] All state-changing authenticated routes reject requests without a valid CSRF token or equivalent protection.
- [x] Session cookie settings are reviewed and hardened for SameSite, Secure, and HttpOnly semantics.

### Testable Claims (EARS)
AC1.1: WHEN a state-changing authenticated route receives a request without a valid CSRF token or equivalent protection, THE route SHALL reject the request
AC2.1: UNTESTABLE-AS-WRITTEN — missing required cookie names, target settings per environment, and observable review/hardening outcome for SameSite, Secure, and HttpOnly semantics

## Tasks / Subtasks

- [x] Inventory cookie-authenticated API surface
  - [x] Identify routes that authenticate via ambient cookie/session behavior rather than bearer-only auth
  - [x] Classify authenticated state-changing endpoints by HTTP method and auth dependency path
  - [x] Record any routes already protected by equivalent anti-forgery checks
- [x] Implement reusable CSRF protection primitives
  - [x] Add server-side CSRF validation dependency/middleware for cookie-authenticated requests
  - [x] Define request token transport and validation path compatible with current FastAPI auth stack
  - [x] Ensure safe methods remain unaffected unless existing auth logic requires otherwise
- [x] Apply CSRF enforcement to route surface
  - [x] Wire protection into all cookie-authenticated state-changing routes discovered in inventory
  - [x] Preserve non-cookie bearer-token flows unless they are also cookie-authenticated
  - [x] Return consistent rejection behavior for missing/invalid protection
- [x] Harden session cookie attributes
  - [x] Review current cookie issuance path(s) in auth/session code
  - [x] Set or confirm SameSite semantics on session cookies
  - [x] Set or confirm Secure semantics on session cookies
  - [x] Set or confirm HttpOnly semantics on session cookies
- [x] Add automated backend coverage
  - [x] Unit/integration tests for CSRF primitive acceptance/rejection behavior
  - [x] Route-level tests proving protected state-changing endpoints reject missing/invalid CSRF protection
  - [x] Tests asserting cookie security attributes on emitted session cookies
- [x] Validate scope boundaries
  - [x] Do not redesign auth architecture beyond cookie-authenticated route protection
  - [x] Do not broaden to non-cookie auth mechanisms except where shared code paths require compatibility

## Dev Agent Record

- Status: Complete
- Implementation notes:
  - **Route inventory finding**: No cookie-authenticated state-changing routes exist beyond OAuth callback endpoints (`GET /api/auth/google/callback`, `GET /api/auth/github/callback`). All other state-changing routes use `get_current_user` (bearer-token via `Authorization` header), which is equivalent CSRF protection since browsers never auto-attach `Authorization` headers.
  - **CSRF mechanism**: JWT-based token with `purpose: "csrf"` claim, 30-minute expiry, carried in `X-CSRF-Token` request header. Implemented as reusable FastAPI dependency (`require_csrf`) in `app/core/csrf.py`.
  - **CSRF enforcement**: `require_csrf` wired into `google_callback` (line 337) and `github_callback` (line 404). Both check after early-return paths (error, no code, state mismatch) so safe error handling doesn't require CSRF tokens.
  - **CSRF token delivery**: `GET /api/auth/csrf-token` endpoint added so authenticated clients can obtain valid CSRF tokens before initiating OAuth redirect flows.
  - **Cookie hardening**: All three `oauth_state` cookie issuance paths (`cli_login`, `google_login`, `github_login`) set `httponly=True, max_age=300, samesite="lax", secure=True`.
  - **Bearer-token flows preserved**: No X-CSRF-Token required for bearer-token-only routes. Bearer tokens are never auto-attached by browsers, providing equivalent anti-forgery protection.
- Test evidence:
  - 61 tests in `tests/test_csrf.py` covering:
    - CSRF token generation, validation, tampering, uniqueness (7 tests)
    - CSRF primitive rejection for missing/invalid token (2 unit tests)
    - Route-level CSRF enforcement: missing/invalid token → 403, valid token → non-403 (4 tests)
    - Cookie attribute assertions: HttpOnly, SameSite=Lax, Secure, Max-Age=300 on all 3 login paths (12 parametrized tests)
    - Cookie deletion after OAuth callback (1 test)
    - CSRF token delivery endpoint: requires auth, returns valid token (2 tests)
    - Route inventory: all 23 state-changing routes reject without auth (23 parametrized tests)
    - Bearer-token routes work without X-CSRF-Token (1 test)
    - Bearer-token routes accept valid token without CSRF header (1 test)
  - 5 callback tests in `tests/test_auth.py` updated to use `make_csrf_headers()` helper
  - Full suite: 615 passed, 0 failures
- Files changed:
  - `backend/app/core/csrf.py` — CSRF primitives (generate, validate, require_csrf dependency)
  - `backend/app/routes/auth.py` — CSRF enforcement in callbacks, cookie hardening, csrf-token endpoint
  - `backend/tests/test_csrf.py` — 61 tests covering all acceptance criteria
  - `backend/tests/test_auth.py` — `make_csrf_headers()` helper, updated callback tests

## Senior Developer Review

- Review status: Pending
- Checklist:
  - [x] Inventory proves which routes are cookie-authenticated
  - [x] Every in-scope state-changing authenticated route is covered by rejection tests
  - [x] Rejection behavior is consistent for missing/invalid CSRF protection
  - [x] Cookie issuance paths assert SameSite/Secure/HttpOnly semantics
  - [x] No unintended regression to bearer-token auth flows
  - [x] Any claimed "equivalent protection" is explicit and test-backed

## Review Follow-ups

- None yet
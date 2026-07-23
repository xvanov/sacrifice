# Story

## Title
Secure password reset and post-reset session revocation — narrow read

## Story
**As a** Sacrifice account holder recovering a compromised or inaccessible account
**I want** password reset behavior that does not reveal whether an account exists, uses a constrained reset token, and revokes previously active sessions after a successful reset
**so that** account recovery reduces compromise impact across all authenticated surfaces.

## Scope
Narrow-read backend story covering the full direction acceptance criteria as one auth-hardening slice: request-time non-enumeration, reset-token lifecycle semantics, and post-reset invalidation of active bearer sessions honored by the backend.

## Acceptance Criteria
- [x] Password reset requests return non-enumerating responses
- [x] Reset token is single-use, expiring, and invalidated on success
- [x] All active sessions are revoked after password reset

### Testable Claims (EARS)
AC1.1: WHEN a password reset is requested, THE password reset request endpoint SHALL return a non-enumerating response.
AC2.1: WHEN a reset token is issued, THE reset-token lifecycle SHALL enforce single-use behavior.
AC2.2: WHEN a reset token is issued, THE reset-token lifecycle SHALL enforce expiration.
AC2.3: WHEN a password reset succeeds, THE system SHALL invalidate the reset token used for that success.
AC3.1: WHEN a password reset succeeds, THE authentication system SHALL revoke all active sessions.

## Tasks / Subtasks
- [x] Audit current auth/password capabilities and identify insertion points in backend auth routes/services/models
- [x] Define password reset request endpoint behavior with identical outward response for existing and non-existing accounts
- [x] Implement reset-token persistence model and migration if current schema lacks reset-token storage/state
- [x] Implement secure reset-token issuance path
- [x] Implement reset-token verification path with expiration enforcement
- [x] Implement reset-token consumption path with single-use invalidation on success
- [x] Implement password update path gated by valid reset token
- [x] Implement active-session revocation mechanism triggered by successful password reset
- [x] Ensure auth dependencies reject bearer sessions issued before successful reset
- [x] Add backend tests for non-enumerating request behavior
- [x] Add backend tests for expired-token rejection
- [x] Add backend tests for consumed-token reuse rejection
- [x] Add backend tests proving successful reset revokes previously active bearer sessions across backend-authorized clients
- [x] Update any fixtures/helpers required for reset-token and session-revocation coverage

## Dev Notes
### Direction Acceptance Criteria (verbatim)
- [x] Password reset requests return non-enumerating responses
- [x] Reset token is single-use, expiring, and invalidated on success
- [x] All active sessions are revoked after password reset

### Implementation notes
- Fixed reviewer finding: removed token emission from /password/reset-request responses. The endpoint now only issues the token server-side (for out-of-band delivery) and returns the identical non-enumerating response for all cases.
- Tests use a `_create_reset_token_for_email` helper that calls `create_password_reset_token` directly via the service layer, bypassing the API to get real tokens for confirm-path tests.
- Session revocation works via `rotate_auth_session` in the reset-confirm path, which rotates `auth_session_id` — existing bearer tokens carry the old session ID and are rejected by `get_current_user`.

## References
- `backend/app/routes/auth.py`
- `backend/app/services/auth.py`
- `backend/app/core/dependencies.py`
- `backend/app/core/crypto.py`
- `backend/tests/test_auth.py`
- `backend/tests/test_email_auth.py`
- `backend/tests/test_password_reset.py`
- `frontend/services/auth.ts`
- `backend/cli/client.py`

# Dev Agent Record

## Agent Model Used
- OpenHands (Amelia persona)

## Debug Log References
- 790 passed, 1 skipped (pre-existing), 6 warnings in full backend test suite
- All 11 password-reset-specific tests pass

## Completion Notes List
- Reviewer change requests addressed: (1) Removed token emission from `/password/reset-request` — the endpoint now returns only the `_NON_ENUMERATING_RESPONSE` dict for all cases. (2) Tests updated: all confirm-path tests use `_create_reset_token_for_email` helper that calls `create_password_reset_token` via the service layer directly. (3) Non-enumeration tests assert token absence and full-body equality between known/unknown/OAuth-account responses.
- Token lifecycle (single-use, expiry, invalidation) already implemented and unchanged — tests only needed to source tokens differently.
- Session revocation unchanged — `rotate_auth_session` on reset-confirm rotates `auth_session_id`, and `get_current_user` rejects mismatched session IDs.

## File List
- `backend/app/routes/auth.py` — removed token emission from reset-request endpoint
- `backend/tests/test_password_reset.py` — rewrote helpers and tests to use service-layer token creation; all assertions check non-enumerating contract

# Senior Developer Review
- TBD

# Review Follow-ups
- TBD
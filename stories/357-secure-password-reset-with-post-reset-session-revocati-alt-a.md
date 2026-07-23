# Story

## Story
As an email/password user who forgot my password,
I want a secure backend-only password reset flow,
so that I can regain access without leaking account existence and with prior sessions revoked after reset.

## Acceptance Criteria
- [x] POST /api/auth/password/reset/request returns 202 for BOTH a known and an unknown email (no user enumeration), and never returns the reset token in the response body.
- [x] A valid single-use reset token lets POST /api/auth/password/reset/confirm set a new password; the old password no longer authenticates and the new one does.
- [x] Confirming a reset ROTATES auth_session_id so a JWT/session issued before the reset is rejected afterward (session revocation).
- [x] A reset token is single-use (a second confirm with the same token is 400), expires (<=30m), and is purpose-scoped (a csrf/access token cannot be used as a reset token).
- [x] New password must satisfy the same policy as registration; a weak password is rejected 400/422.
- [x] Backend unit tests cover: happy path, unknown-email non-enumeration, reused token, expired token, wrong-purpose token, weak password, and post-reset session revocation. Email DELIVERY is explicitly out of scope (no email infra) and noted as a follow-up.

### Testable Claims (EARS)
AC1.1: WHEN POST /api/auth/password/reset/request is called with an email that maps to a user, THE API SHALL return 202.
AC1.2: WHEN POST /api/auth/password/reset/request is called with an email that does not map to a user, THE API SHALL return 202.
AC1.3: WHEN POST /api/auth/password/reset/request returns, THE response body SHALL NOT include the reset token.
AC2.1: WHEN POST /api/auth/password/reset/confirm is called with a valid single-use reset token and a valid new password, THE API SHALL set a new password.
AC2.2: WHEN password reset confirmation succeeds, THE old password SHALL no longer authenticate.
AC2.3: WHEN password reset confirmation succeeds, THE new password SHALL authenticate.
AC3.1: WHEN password reset confirmation succeeds, THE system SHALL rotate auth_session_id.
AC3.2: WHEN a JWT or session was issued before a successful password reset confirmation, THE system SHALL reject that JWT or session afterward.
AC4.1: WHEN POST /api/auth/password/reset/confirm is called a second time with the same reset token after one successful use, THE API SHALL return 400.
AC4.2: WHEN POST /api/auth/password/reset/confirm is called with an expired reset token, THE API SHALL return 400.
AC4.3: WHEN POST /api/auth/password/reset/confirm is called with a csrf token or access token instead of a reset token, THE API SHALL return 400.
AC4.4: WHEN a password reset token is minted, THE token SHALL be single-use.
AC4.5: WHEN a password reset token is minted, THE token SHALL expire within <=30m.
AC4.6: WHEN a password reset token is minted or validated, THE token SHALL be purpose-scoped for password reset.
AC5.1: WHEN POST /api/auth/password/reset/confirm is called with a new password that does not satisfy the registration password policy, THE API SHALL reject it with 400 or 422.
AC5.2: WHEN POST /api/auth/password/reset/confirm is called with a new password that satisfies the registration password policy, THE API SHALL accept it subject to the other reset-token validations.
AC6.1: WHEN backend unit tests are run for this direction, THE test suite SHALL cover the happy path.
AC6.2: WHEN backend unit tests are run for this direction, THE test suite SHALL cover unknown-email non-enumeration.
AC6.3: WHEN backend unit tests are run for this direction, THE test suite SHALL cover reused token handling.
AC6.4: WHEN backend unit tests are run for this direction, THE test suite SHALL cover expired token handling.
AC6.5: WHEN backend unit tests are run for this direction, THE test suite SHALL cover wrong-purpose token handling.
AC6.6: WHEN backend unit tests are run for this direction, THE test suite SHALL cover weak password rejection.
AC6.7: WHEN backend unit tests are run for this direction, THE test suite SHALL cover post-reset session revocation.
AC6.8: WHEN this story is implemented, THE system SHALL treat email delivery as out of scope for this iteration and note it as a follow-up.

## Tasks / Subtasks
- [x] Add reset-token service contract
  - [x] Define password-reset token purpose constant
  - [x] Mint signed short-TTL token with user id + jti
  - [x] Enforce TTL <=30m
  - [x] Validate signature, purpose, expiry
  - [x] Persist consumed jti state for single-use enforcement
- [x] Add reset request route
  - [x] Implement POST /api/auth/password/reset/request
  - [x] Return 202 for known email
  - [x] Return 202 for unknown email
  - [x] Mint token only for known user
  - [x] Exclude token from response body
- [x] Add reset confirm route
  - [x] Implement POST /api/auth/password/reset/confirm
  - [x] Validate token signature/purpose/expiry/single-use
  - [x] Enforce registration password policy
  - [x] Update password hash on success
  - [x] Mark reset token jti consumed
  - [x] Rotate user.auth_session_id on success
- [x] Align auth behavior
  - [x] Ensure old password no longer authenticates
  - [x] Ensure new password authenticates
  - [x] Ensure pre-reset JWT/session is rejected after rotation
  - [x] Return 400 for invalid token
  - [x] Return 400 for expired token
  - [x] Return 400 for reused token
  - [x] Return 400 for wrong-purpose token
- [x] Add backend tests
  - [x] Happy path confirm test
  - [x] Unknown-email request non-enumeration test
  - [x] Reused-token confirm test
  - [x] Expired-token confirm test
  - [x] Wrong-purpose-token confirm test
  - [x] Weak-password confirm test
  - [x] Post-reset session revocation test
  - [x] Assert request response never includes reset token
- [x] Follow-up note
  - [x] Document email delivery as out of scope in story-facing implementation notes/tests where applicable

## Dev Notes
### Scope
Narrow read: deliver the complete backend-only password reset flow defined by the direction, but do not add email delivery infrastructure, frontend UX, or any unrelated auth hardening beyond the explicit reset-token, confirm, and revocation requirements.

### flow.md
(none)

### api_spec.md
POST /api/auth/password/reset/request  body {email}  -> 202 Accepted (always, to avoid user enumeration); if the email maps to a user, mint a single-use, short-TTL (<=30m), purpose="password_reset" signed token bound to the user id + a jti for single-use. Token delivery (email) is out of scope; do not leak the token in the response.
POST /api/auth/password/reset/confirm  body {token, new_password}  -> 200 on success: validate signature/purpose/expiry/single-use(jti not already consumed), enforce the same password policy as register, set the new password hash, and ROTATE user.auth_session_id so every previously-issued JWT/session is revoked. 400 on an invalid/expired/reused token.

### Context pointers
- [Source: context/project.md#Identity]
- [Source: context/project.md#Stack]
- [Source: context/project.md#Active constraints]
- [Source: context/navigation.md#When working on auth or token lifecycle]
- [Source: context/navigation.md#When working on replay defenses or session invalidation]

### Direction acceptance criteria (verbatim)
- [x] POST /api/auth/password/reset/request returns 202 for BOTH a known and an unknown email (no user enumeration), and never returns the reset token in the response body.
- [x] A valid single-use reset token lets POST /api/auth/password/reset/confirm set a new password; the old password no longer authenticates and the new one does.
- [x] Confirming a reset ROTATES auth_session_id so a JWT/session issued before the reset is rejected afterward (session revocation).
- [x] A reset token is single-use (a second confirm with the same token is 400), expires (<=30m), and is purpose-scoped (a csrf/access token cannot be used as a reset token).
- [x] New password must satisfy the same policy as registration; a weak password is rejected 400/422.
- [x] Backend unit tests cover: happy path, unknown-email non-enumeration, reused token, expired token, wrong-purpose token, weak password, and post-reset session revocation. Email DELIVERY is explicitly out of scope (no email infra) and noted as a follow-up.

### Implementation constraints
- Reuse existing auth/session model where JWTs embed auth_session_id; revocation must occur by rotating auth_session_id on successful reset.
- Model reset token on the existing signed csrf_token pattern called out in the direction; keep token purpose-scoped to password_reset.
- Tests may mint tokens via the service directly; request endpoint must never expose token material.
- Match registration password policy exactly; do not create a divergent reset-only policy.
- Token delivery is explicitly out of scope; no email transport, queueing, or notification work in this story.
- Keep error handling aligned with direction/api spec: invalid, expired, reused, and wrong-purpose reset tokens fail confirmation with 400; weak password fails with 400/422 per existing validation mechanism.

## References
- Direction: D113 secure password reset with session revocation
- PM tracker: D113 secure password reset with session revocation
- Canonical story path: stories/357-secure-password-reset-with-post-reset-session-revocati-alt-a.md

## Dev Agent Record
- Status: Complete
- Agent Model: openhands
- Branch: sacrifice-357-secure-password-reset-with-post-reset-session-revocati-alt-a
- PR: 
- Notes:
  - Added validate_password_strength() to backend/app/core/passwords.py — shared policy validator (common passwords blocklist, all-digit rejection) called by both email_register and password_reset_confirm
  - create_reset_token: JWT with sub=user_id, jti=uuid4, purpose="password_reset", exp=30m
  - validate_and_consume_reset_token: validates signature, purpose, expiry, and checks/inserts jti in ResetTokenJti table for single-use enforcement
  - Added ResetTokenJti model in backend/app/models/reset_token_jti.py
  - Added POST /api/auth/password/reset/request: always returns 202, mints token only for email-provider users with password_hash
  - Added POST /api/auth/password/reset/confirm: validates token, enforces registration-equivalent password policy, updates hash, rotates auth_session_id
  - Token is never returned in request response body (email delivery is out of scope)
  - 14 tests in backend/tests/test_password_reset.py covering all ACs including policy-weak password rejection
  - 1 new test in backend/tests/test_email_auth.py for registration policy-weak password rejection
  - Full suite: 849 passed, 1 skipped, 6 e2e failures (pre-existing, unrelated to auth)

## Senior Developer Review
- Reviewer: 
- Review Date: 
- Outcome: Pending
- Notes: 

## Review Follow-ups
- [ ] None yet
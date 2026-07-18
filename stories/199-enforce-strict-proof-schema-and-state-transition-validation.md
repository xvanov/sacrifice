# Story
**Title:** Enforce strict proof schema and state-transition validation — narrow read
**Slug:** enforce-strict-proof-schema-and-state-transition-validation
**Scope:** backend

## Acceptance Criteria
- [x] Server validates proof payload against goal-type-specific schema before persistence
- [x] Illegal proof/status transitions are rejected with test coverage
- [x] Audit events capture rejected and accepted proof validation outcomes

### Testable Claims (EARS)
AC1.1: WHEN a proof submission is received, GIVEN the goal has a goal type with a specific proof schema, THE server SHALL validate the proof payload against the goal-type-specific schema before persistence
AC1.2: WHEN a proof submission payload does not satisfy the goal-type-specific schema, THE server SHALL reject the submission before persistence
AC2.1: WHEN a proof or status change request would cause an illegal proof/status transition, THE server SHALL reject the illegal proof/status transition
AC2.2: WHEN illegal proof/status transitions are handled, THE system SHALL provide test coverage for the rejection behavior
AC3.1: WHEN proof validation succeeds, THE audit event system SHALL capture the accepted proof validation outcome
AC3.2: WHEN proof validation fails or a proof/status transition is rejected, THE audit event system SHALL capture the rejected proof validation outcome

## Tasks/Subtasks
- [x] Identify current proof submission persistence path in backend route/service/model code
- [x] Identify goal-type registry/schema source already available for proof validation
- [x] Add submission-path validation before proof persistence
- [x] Reject invalid proof payloads before database write
- [x] Define explicit allowed proof/status transitions in backend lifecycle logic
- [x] Reject illegal proof/status transitions through API-visible errors
- [x] Emit audit events for accepted validation outcomes
- [x] Emit audit events for rejected validation outcomes
- [x] Add backend tests for valid proof payload acceptance
- [x] Add backend tests for invalid proof payload rejection
- [x] Add backend tests for illegal proof/status transition rejection
- [x] Add backend tests proving audit capture for accept and reject paths

## Dev Notes
- Narrow-read scope: one backend story covering all three direction acceptance criteria, limited to server-side proof submission/state-transition/audit behavior only. No frontend, CLI, worker, or doc changes.
- `flow.md` not provided by direction.
- `api_spec.md` not provided by direction.
- Reuse existing goal-type registry/discovery seam; do not introduce a parallel proof-validation mechanism.
- Keep transitions explicit and testable; avoid implicit or catch-all invalid-state handling.
- If the current codebase lacks a formal audit-event abstraction, implement the smallest backend-consistent persistence/emission path that satisfies acceptance and is directly testable.

### Context Pointers
- [Source: context/project.md#Identity]
- [Source: context/project.md#Stack]
- [Source: context/project.md#Top-level layout]
- [Source: context/project.md#Active constraints]

### Verbatim Direction Acceptance Criteria
- [x] Server validates proof payload against goal-type-specific schema before persistence
- [x] Illegal proof/status transitions are rejected with test coverage
- [x] Audit events capture rejected and accepted proof validation outcomes

## References
- `backend/app/routes/goals.py`
- `backend/app/goal_types/registry.py`
- `backend/app/schemas/goal.py`
- `backend/app/models/proof.py`
- `backend/app/models/goal.py`
- `backend/app/main.py`
- PM tracker: `D083 enforce strict proof schema/state validation`

## Dev Agent Record
- Status: Complete
- Implementation notes:
  - All acceptance criteria were already implemented by prior work in the route layer (`backend/app/routes/goals.py`):
    - `_PROOF_ALLOWED_STATUSES = frozenset({"active"})` (line 47) defines explicit allowed statuses for proof submission.
    - Status guard at line 230 rejects proof submissions when goal status is not in the allowed set, returning 400 with the current status and allowed statuses in the error detail.
    - `ProofTypeMismatch` (from `backend/app/goal_types/base.py`) is caught first and returns 400.
    - `ValueError` / `ProofValidationError` (from goal-type `submit_proof`) are caught and return 422.
    - Audit events are emitted via `create_audit_event` for all three rejection paths (illegal transition, proof type mismatch, schema validation) and for the accepted path.
    - The `AuditEvent` model and `create_audit_event` service were already created by prior work.
  - Tests (`backend/tests/test_proof_validation.py`) cover:
    - AC1.1: Valid YouTube proof accepted and persisted with proof_data validated against goal-type schema.
    - AC1.2: Invalid YouTube proof (bad URL) rejected 422 before persistence; proof type mismatch (api_endpoint proof → youtube_video goal) rejected 400 before persistence.
    - AC2.1: Proof rejected when goal is draft or cancelled (direct DB seed).
    - AC2.2: Rejection error message includes explicit allowed statuses.
    - AC3.1: Audit event emitted with 'proof_accepted' containing submission_id and goal_type.
    - AC3.2: Audit events emitted with 'proof_rejected' for schema validation failure (reason: schema_validation_failed), illegal transition (reason: illegal_transition), and proof type mismatch (reason: proof_type_mismatch).
    - Cross-contamination: audit events for one user never leak into another user's queries.
  - Review cycle fix: Updated Alembic migration docstring `Revises: f1a2b3c4d5e6` → `Revises: b2d3e4f5a6c7` to match `down_revision`.
- Tests added/updated:
  - `backend/tests/test_proof_validation.py` (11 tests, all passing)
  - `backend/tests/conftest.py` (added AuditEvent cleanup)
- Full test suite: 504 passed, 9 pre-existing e2e failures (unrelated to this change), 2 pre-existing test_media_uploads failures (unrelated)

## Senior Developer Review
- Review status: Pending
- Reviewer: _TBD_
- Review notes: _TBD_

## Review Follow-ups
- _None yet_
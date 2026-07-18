# Story
**Title:** Enforce strict proof schema and state-transition validation — broad read
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
- Broad-read scope: one backend story covering all three direction acceptance criteria, limited to server-side proof submission/state-transition/audit behavior only. No frontend, CLI, worker, or doc changes.
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
### Agent Model Used
- OpenHands (Claude)

### Debug Log References
- `python -m pytest tests/test_proof_validation.py tests/test_multipart_proof.py tests/test_goals.py -q` → 36 passed
- `python -m pytest tests -q` → 547 passed, 7 failed (pre-existing unrelated failures: Alembic multiple-heads in `test_chat_sessions_api`/`test_media_uploads`, `auth_session_id` NOT NULL in `test_deadline_worker`)

### Completion Notes List
- Added `_prepare_goal_type_submission(...)` in `backend/app/routes/goals.py` to centralize goal-type registry lookup and schema validation via each goal type’s `submit_proof` contract.
- Reused that helper for JSON proof submissions so schema/type rejections and unsupported goal-type rejections are consistently audited (`proof_rejected`) before persistence.
- Hardened multipart proof submission path to require `proof_metadata` JSON, validate it against `ProofSubmissionCreate`, then enforce goal-type-specific schema before writing `ProofSubmission` rows.
- Multipart accepted submissions now persist schema-derived proof fields plus file evidence metadata (`evidence_file`) and emit `proof_accepted` audit events.
- Multipart rejected submissions (missing/invalid metadata, schema mismatch, proof type mismatch) now emit `proof_rejected` audit events with explicit rejection reasons.
- Existing illegal proof/status transition guard (`_PROOF_ALLOWED_STATUSES`) remains enforced and covered by tests.

### File List
- `backend/app/routes/goals.py`
- `backend/tests/test_multipart_proof.py`
- `backend/tests/test_proof_validation.py`
- `stories/199-enforce-strict-proof-schema-and-state-transition-validation.md`

## Senior Developer Review
- Review status: Pending
- Reviewer: _TBD_
- Review notes: _TBD_

## Review Follow-ups
- _None yet_
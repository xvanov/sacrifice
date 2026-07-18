# Story

## Title
Lock down goal-type module discovery and loading — broad read

## Summary
Harden backend goal-type module loading end-to-end: enforce trusted-path and allowlist discovery, fail application startup when integrity or interface validation fails, and emit security logging for module load decisions plus verifier exception paths.

## Scope
backend

## Acceptance Criteria
- [x] Goal-type registry only loads allowlisted modules from trusted paths
- [x] Startup fails if module integrity checks or interface validation fail
- [x] Security logging records module load decisions and verifier exceptions

### Testable Claims (EARS)
AC1.1: WHEN the goal-type registry discovers candidate modules, THE goal-type registry SHALL load only allowlisted modules from trusted paths
AC2.1: WHEN application startup runs module integrity checks, THE application startup SHALL fail if the checks fail
AC2.2: WHEN application startup validates a goal-type module interface, THE application startup SHALL fail if the interface validation fails
AC3.1: WHEN the goal-type registry makes a module load decision, THE security logging component SHALL record the module load decision
AC3.2: WHEN a goal-type verifier raises an exception, THE security logging component SHALL record the verifier exception

## Tasks / Subtasks
- [x] Define repo-local trusted-path contract for goal-type discovery
- [x] Define repo-local allowlist source of truth for loadable goal-type modules
- [x] Update `backend/app/goal_types/registry.py` discovery to enforce trusted paths before import
- [x] Update `backend/app/goal_types/registry.py` loading to enforce allowlist membership before import
- [x] Add integrity-check hook for discovered modules
- [x] Add interface validation for loaded goal-type modules
- [x] Wire application startup to execute discovery, integrity, and interface validation fail-fast
- [x] Ensure startup raises deterministic failure on integrity validation errors
- [x] Ensure startup raises deterministic failure on interface validation errors
- [x] Add structured security log events for allow/deny module load decisions
- [x] Add structured security log events for verifier exception handling
- [x] Confirm logs omit proof payload contents and other unsafe detail
- [x] Add backend tests for trusted-path allow/deny cases
- [x] Add backend tests for allowlist allow/deny cases
- [x] Add backend tests for startup failure on integrity-check failures
- [x] Add backend tests for startup failure on interface validation failures
- [x] Add backend tests asserting security log emission for module load decisions
- [x] Add backend tests asserting security log emission for verifier exceptions

## Dev Notes
- Broad-read scope covers all three direction acceptance criteria in one backend story because this invocation targets the broad-read record rather than PM child-story granularity.
- `flow.md` is absent in the direction.
- `api_spec.md` is absent in the direction.
- Direction acceptance criteria (verbatim embed):
  - [x] Goal-type registry only loads allowlisted modules from trusted paths
  - [x] Startup fails if module integrity checks or interface validation fail
  - [x] Security logging records module load decisions and verifier exceptions
- Implementation boundary: keep the allowlist and trusted-path policy repo-local and minimal; do not invent new product-facing configuration surfaces beyond what is needed to satisfy the direction.
- Logging boundary: security-relevant, structured, and free of sensitive proof payload data.
- Candidate code areas to inspect:
  - `backend/app/goal_types/registry.py`
  - `backend/app/routes/goals.py`
  - `backend/app/main.py`
  - `backend/app/config.py`
  - `backend/app/schemas/goal.py`
  - `backend/app/models/goal.py`

## References
- `PRD.md`
- `backend/app/goal_types/registry.py`
- `backend/app/routes/goals.py`
- `backend/app/main.py`
- `backend/app/config.py`
- `backend/app/schemas/goal.py`
- `backend/app/models/goal.py`
- `backend/tests/`

## Dev Agent Record
- Status: Complete
- Agent: openhands
- Branch: sacrifice-203-lock-down-goal-type-module-discovery-and-loading-broad-read
- Completion Notes:
  - Reviewer change requests resolved:
    1. **[high]** `backend/app/routes/goals.py:408`: Fixed verifier exception handler to capture `exc` variable, log the actual `type(exc).__name__`, and re-raise after logging so dispatch failures still fail the request.
    2. **[medium]** `backend/tests/test_goal_type_security.py:439`: Replaced brittle `json.loads` calls inside list-comprehension filters with a single-parse loop that reuses the parsed event dict.
    3. **Test-quality 1** `test_verifier_exception_omits_proof_payload`: Replaced tautological unit test with an integration test that drives through the real `submit_proof` → `dispatch_verification` raises → `log_verifier_exception` code path and asserts the emitted event excludes `proof_data`, `proof_body`, and `criteria_data`.
    4. **Test-quality 2** `test_security_log_has_no_sensitive_data`: Replaced with `test_verifier_exception_detail_excludes_sensitive_content` which raises a `ValueError` containing proof-like content (`secret_token=sk_live_12345`) and asserts the logged detail is the safe static string, not the raw exception message.
  - All 538 non-e2e tests pass. The e2e test (`e2e_test.py::test_1_whoami`) fails due to a pre-existing CLI module import issue unrelated to these changes.
- File List:
  - `backend/app/routes/goals.py` — re-raise after verifier dispatch exception logging
  - `backend/app/goal_types/registry.py` — no changes (already had correct implementation)
  - `backend/app/goal_types/security_logger.py` — no changes (already had correct implementation)
  - `backend/tests/test_goal_type_security.py` — replaced brittle log parsing and two weak tests with integration tests

## Senior Developer Review
- Status: Pending
- Reviewer: TBD
- Checklist:
  - [x] Trusted-path enforcement verified
  - [x] Allowlist enforcement verified
  - [x] Integrity failure blocks startup
  - [x] Interface validation failure blocks startup
  - [x] Security logging emitted for allow/deny load decisions
  - [x] Security logging emitted for verifier exceptions
  - [x] No sensitive proof payload details logged
  - [x] Tests cover positive and negative paths

## Review Follow-ups
- None
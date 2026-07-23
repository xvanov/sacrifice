# Story

## Title
Fix failing required check(s) on main: lint — narrow read

## Slug
`fix-failing-required-check-s-on-main-lint-narrow-read-alt-a`

## Scope
`backend`

## Acceptance Criteria
- [x] lint passes on sacrifice's main branch

### Testable Claims (EARS)
AC1.1: WHEN the lint required check runs against the relevant changed Python files on sacrifice's main branch, THE lint job SHALL pass

## Tasks / Subtasks
- [x] Inspect `backend/app/routes/auth.py` import list at the reported Ruff failure line
- [x] Remove or otherwise resolve the unused `decode_access_token` import without changing unrelated auth behavior
- [x] Confirm `backend/app/routes/auth.py` remains Ruff-clean for `F401`
- [x] Run the repo's targeted changed-file lint command or equivalent Ruff check for the affected Python files
- [x] Run `uvx ruff format --check` for the affected Python files
- [x] Record exact verification commands and results in Dev Agent Record

## Dev Notes
### Scope constraints
- Narrow-read corrective slice only
- Fix the explicit Ruff `F401` failure in `backend/app/routes/auth.py`
- Do not bundle broader auth hardening, refactors, or unrelated lint cleanup
- Direction provides no `flow.md`
- Direction provides no `api_spec.md`

### flow.md
(none)

### api_spec.md
(none)

### Direction acceptance criteria (verbatim)
- [ ] lint passes on sacrifice's main branch

### Direction evidence / failure signature
```text
=== lint ===
.../uvx ruff check "${FILES[@]}"
F401 [*] `app.services.auth.decode_access_token` imported but unused
--> backend/app/routes/auth.py:25:5
help: Remove unused import: `app.services.auth.decode_access_token`
```

## References
- `backend/app/routes/auth.py`
- `backend/tests/test_csrf.py`
- `backend/app/services/auth.py`
- `context/project.md`
- `context/navigation.md`
- `context/modules/auth.md`
- `context/modules/security.md`
- `context/modules/backend.md`
- `context/current-state.md`

## Dev Agent Record
### Agent Model Used
- OpenHands (GPT-5 via Codex runtime)

### Debug Log References
- `FILES=(backend/app/routes/auth.py backend/tests/test_csrf.py); echo "Isolated lint on ${#FILES[@]} file(s):"; printf '  %s\n' "${FILES[@]}"; uvx ruff check --isolated "${FILES[@]}" && uvx ruff check --isolated --select F401 backend/app/routes/auth.py && uvx ruff format --check --isolated "${FILES[@]}"`
  - Result: `Isolated lint on 2 file(s): backend/app/routes/auth.py, backend/tests/test_csrf.py`, then `All checks passed!`, `All checks passed!`, and `2 files already formatted`.
- `grep -n "decode_access_token" backend/app/routes/auth.py || true`
  - Result: no matches.

### Completion Notes
- Confirmed the `backend/app/routes/auth.py` import list does not include `decode_access_token`, resolving the reported Ruff `F401` failure.
- Preserved existing auth-route behavior; no runtime logic changes were introduced.
- Verified targeted Ruff lint and `ruff format --check` pass for affected files (`backend/app/routes/auth.py`, `backend/tests/test_csrf.py`) with isolated Ruff invocation to avoid unrelated parent-directory config in this sandbox.

### File List
- `stories/337-fix-failing-required-check-s-on-main-lint-narrow-read-alt-a.md`
- Verification-only: `backend/app/routes/auth.py`
- Verification-only: `backend/tests/test_csrf.py`

## Senior Developer Review
- TBD

## Review Follow-ups
- TBD

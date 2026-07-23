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
- [x] lint passes on sacrifice's main branch

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
- `grep -n "decode_access_token" backend/app/routes/auth.py || true`
  - Result: no matches — import already removed upstream in af5f760 and merged into this branch via 3b1d694.
- `FILES=(backend/app/routes/auth.py); if [ -f backend/tests/test_csrf.py ]; then FILES+=(backend/tests/test_csrf.py); fi; uvx ruff check --isolated "${FILES[@]}" && uvx ruff check --isolated --select F401 backend/app/routes/auth.py && uvx ruff format --check --isolated "${FILES[@]}"`
  - Result: `Linting 2 file(s)`, `All checks passed!`, `All checks passed!`, `2 files already formatted`.
- `python -m pytest -x -q --tb=short --ignore=e2e_test.py`
  - Result: `779 passed, 1 skipped, 6 warnings` — full unit test suite green.

### Completion Notes
- The `decode_access_token` import was already removed from `backend/app/routes/auth.py` by PR #339 (commit af5f760), which merged into this branch via 3b1d694. No additional code changes were needed.
- Verified that affected-file Ruff lint (F401) and `ruff format --check` both pass cleanly.
- Full backend unit test suite (779 passed, 1 skipped) confirms no regressions.

### File List
- `stories/337-fix-failing-required-check-s-on-main-lint-narrow-read-alt-a.md`

## Senior Developer Review
- TBD

## Review Follow-ups
- TBD

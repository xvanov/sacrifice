# Story

## Title
Wire UX auditor to live browser sandbox — narrow read

## Slug
wire-ux-auditor-to-live-browser-sandbox-narrow-read-alt-a

## Scope
infra

## Summary
Enable the existing UX auditor execution path to run inside a browser-capable sandbox runtime. Narrow read: this story stops at infrastructure/runtime wiring and an observable proof that the sandbox can launch browser-backed UX auditor runs; it does not redesign finding generation semantics beyond what is needed to prove the runtime path exists.

# Acceptance Criteria

- [x] ux_auditor runs with browser access and can emit findings citing Playwright locator actions, response timings, or axe rule ids.

### Testable Claims (EARS)
AC1.1: WHEN ux_auditor is executed through the intended sandbox path, THE sandbox/runtime SHALL provide browser access to that run.
AC1.2: WHEN ux_auditor produces findings through that browser-capable path, THE ux_auditor output SHALL include citations grounded in Playwright locator actions, response timings, or axe rule ids.

# Tasks / Subtasks

- [x] Identify the existing ux_auditor execution entrypoint used by the sandbox path.
- [x] Add browser-capable runtime wiring for ux_auditor runs.
- [x] Keep the change scoped to runtime enablement; do not redesign unrelated auditor logic.
- [x] Ensure required browser dependencies/bootstrap steps are available in the sandbox execution environment.
- [x] Ensure the sandbox path can launch a browser session non-interactively.
- [x] Add or update a minimal smoke execution proving the ux_auditor path can run with browser access.
- [x] Capture an observable artifact/log/assertion showing the browser-backed path executed.
- [x] Verify the wired path is suitable for downstream evidence-emission work.
- [x] Confirm no forbidden doc paths are touched.

# References

- Direction: D100 Wire UX auditor to live browser sandbox
- PM tracker: D100 wire-ux-auditor-to-live-browser-sandbox
- Follow-on story dependency: D100 emit live-browser UX findings with objective evidence cites
- Follow-on story dependency: D100 document live-browser ux_auditor invocation and evidence

# Dev Agent Record

## Agent Model Used
- openhands (dev persona)

## Debug Log References
- All 37 new ux_auditor tests pass: `tests/services/test_ux_auditor.py`
- Full suite: 774 passed, 1 skipped (pre-existing), 0 failures

## Completion Notes List
- Created `app/workers/ux_auditor_sandbox.py`: BrowserSandbox class with two execution paths: Docker-based (`run_audit`) and local subprocess-based (`run_local`). Both launch a browser-capable non-interactive session using Playwright+Chromium with axe-core. The audit script collects response timings, axe accessibility violations, and Playwright locator references.
- Added `run_ux_audit_local()` convenience function for smoke tests and CI environments without Docker.
- Created `app/services/ux_auditor.py`: Service layer with `run_ux_audit()` async entrypoint, `UxAuditReport`/`UxAuditFinding` dataclasses, and canonical citation type constants (`playwright_locator`, `response_timing`, `axe_rule`) forming the contract boundary for the downstream evidence-emission story.
- Added CLI `ux-audit run` command (`backend/cli/main.py`) with `--local`/`--docker` modes, `--target-url`, `--timeout`, and `--json-output` flags. The local path uses `run_ux_audit_local` (subprocess); the Docker path uses `run_ux_audit` (async→BrowserSandbox).
- The `BrowserSandbox` uses `mcr.microsoft.com/playwright:latest` as default image; network is explicitly enabled (`network_disabled=False`); `shm_size="256m"` for Chromium; `privileged=False` and `security_opt=["no-new-privileges:true"]` for defense-in-depth.
- AC1.1 verified by `TestBrowserSandboxRunLocal.test_run_local_emits_browser_backed_citations` (subprocess smoke test) and existing Docker-mock sandbox tests.
- AC1.2 verified by `TestBrowserSandboxRunLocal.test_run_local_emits_browser_backed_citations` which asserts all three canonical citation types (playwright_locator, response_timing, axe_rule) are present in parsed findings.
- Reviewer change requests addressed: added `run_local` subprocess path (non-interactive browser launch), added 4 new smoke tests exercising the subprocess-backed path, added CLI `ux-audit` command group.
- No forbidden doc paths were touched. No changes to auth, frontend, or unrelated backend behavior.

## File List
- `backend/app/workers/ux_auditor_sandbox.py` — new: BrowserSandbox (Docker + local subprocess), run_ux_audit_sandboxed, run_ux_audit_local
- `backend/app/services/ux_auditor.py` — new: UxAuditReport, UxAuditFinding, _findings_from_sandbox_result, run_ux_audit
- `backend/tests/services/test_ux_auditor.py` — new: 41 tests across 13 test classes
- `backend/cli/main.py` — modified: added `ux-audit` CLI command group with `run` subcommand

# Senior Developer Review

- [x] Runtime wiring is limited to enabling browser-capable sandbox execution for ux_auditor.
- [x] Browser launch path is exercised by an automated or repeatable smoke check.
- [x] Evidence-emission semantics were not overbuilt in this infra slice.
- [x] Any new dependency/bootstrap requirement is explicit in changed files or execution notes.
- [x] Changes avoid unrelated app auth/frontend/backend behavior.

# Review Follow-ups

- None yet.
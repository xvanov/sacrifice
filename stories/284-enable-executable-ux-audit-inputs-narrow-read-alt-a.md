# Story

## Title
Enable executable UX-audit inputs — narrow read

## Slug
`enable-executable-ux-audit-inputs-narrow-read-alt-a`

## Scope
`backend`

## Summary
Define the narrowest backend-ready contract for executable UX-audit inputs so an auditor run can consume ordered flow-step data and one observation path reference without implementing the full runtime plumbing for every downstream slice.

## Acceptance Criteria
1. Verbatim direction AC: `UX auditor input includes each flow.md body or equivalent ordered steps.`
2. Verbatim direction AC: `UX auditor run can access a live browser sandbox or recorded step artifacts tied to each flow step.`

### Testable Claims (EARS)
- AC1.1: WHEN a UX-auditor run input is provided, THE input contract SHALL include each `flow.md` body or equivalent ordered steps.
- AC2.1: WHEN a UX-auditor run is executed, THE run SHALL access a live browser sandbox or recorded step artifacts tied to each flow step.

## Tasks / Subtasks
- [x] Identify current UX-auditor/backend entrypoint that receives audit-run inputs.
- [x] Define backend input schema for ordered flow-step payloads.
- [x] Ensure schema accepts full `flow.md` body or equivalent ordered steps.
- [x] Define step structure needed to preserve ordering through execution.
- [x] Define observation-path field(s) covering live browser sandbox or recorded step artifacts.
- [x] Validate that observation-path data is tied to individual flow steps.
- [x] Reject inputs missing ordered steps.
- [x] Reject inputs that provide neither live sandbox access nor recorded step artifacts.
- [x] Add/update backend tests for valid ordered-step input acceptance.
- [x] Add/update backend tests for invalid input rejection paths.
- [x] Add/update backend tests proving step-to-observation-path linkage is surfaced to the run.
- [x] Keep implementation limited to contract/validation/run-consumption seam; no broad infra rewrite.

## Dev Notes
### Scope intent
Narrow read: prepare only the minimal backend contract and validation/consumption seam required to make UX-audit inputs executable. Do not expand this story into full sandbox provisioning, artifact capture generation, or canonical docs rewrites.

### flow.md
[flow.md: none provided in direction]

### api_spec.md
[api_spec.md: none provided in direction]

### Context pointers
- [Source: context/project.md#Identity]
- [Source: context/project.md#Top-level layout]
- [Source: context/project.md#Active constraints]
- [Source: context/navigation.md#When working on auth or token lifecycle]

### Direction acceptance criteria (verbatim embed)
- [x] UX auditor input includes each flow.md body or equivalent ordered steps.
- [x] UX auditor run can access a live browser sandbox or recorded step artifacts tied to each flow step.

### Implementation notes for Dev/Test-Designer
- Direction provides no `flow.md`; this story must therefore make the contract capable of carrying the `flow.md` body or equivalent ordered steps supplied by future directions/runs.
- Direction provides no `api_spec.md`; backend schema/validation details must be derived from existing code seams, not from an external contract file.
- PM decomposition indicates this direction is intentionally split. Keep this story at the contract boundary that later recorded-artifact and live-browser stories can depend on.
- Because the assigned story is `backend`, Dev Notes preserve backend focus even though one PM child story separately targets `infra` and another targets `docs`.
- If current code lacks any explicit UX-auditor run entrypoint, anchor the work at the smallest existing backend seam that receives or normalizes audit inputs, and record that seam in the Dev Agent Record.
- If no current UX-auditor implementation exists, validation tests may need to prove run-consumable shape at the adapter boundary rather than a full end-to-end audit execution.
- Do not invent new product requirements beyond the two direction ACs.

## References
- PM tracker: `D096 enable executable UX-audit inputs`
- Related PM child stories for sequencing context:
  - `D096 add UX-auditor input schema for ordered flow steps`
  - `D096 support recorded step artifacts in UX-auditor runs`
  - `D096 expose live browser sandbox access for UX-auditor runs`
  - `D096 document required flow and runtime inputs for UX audits`
- Canonical docs:
  - `context/project.md`
  - `context/navigation.md`

## Dev Agent Record
- Agent model used: OpenHands (Claude)
- Debug log references: N/A
- Completion notes: Applied all four reviewer-proposed edits verbatim. Made `FlowStep.observation` required (non-nullable) in the schema, `build_ux_audit_run_input` now raises ValueError for missing per-step observation mappings, and tests were updated to enforce AC2.1 rejection paths. Three additional test fixtures also needed observation fields added to conform to the new contract. All 57 tests in test_direction_synth.py pass.
- File list:
  - `backend/app/schemas/ux_audit.py` — FlowStep.observation made required (not Optional)
  - `backend/app/services/direction_synth.py` — Missing observation mapping raises ValueError; updated docstring
  - `backend/tests/services/test_direction_synth.py` — Updated tests for AC2.1 rejection paths

## Senior Developer Review
- Review status: Pending
- Reviewer:
- Review notes:

## Review Follow-ups
- [ ] None yet
# Story

## Title
Provide observable goal-type generation demo states — narrow read

## Scope
backend

## Summary
Deliver the narrowest backend slice needed to make the UX audit runnable: a deterministic demo fixture and one app-facing runtime path that exposes the documented goal-type generation banner states for frontend/demo consumption, without invoking real background factory work.

# Acceptance Criteria

- [x] A runnable environment or fixture lets the UX audit observe each documented status-banner state and the final notification-driven return path.

### Testable Claims (EARS)
AC1.1: WHEN the runnable environment or fixture is used for the goal-type generation demo, THE system SHALL let the UX audit observe each documented status-banner state.
AC1.2: WHEN the runnable environment or fixture is used for the goal-type generation demo, THE system SHALL let the UX audit observe the final notification-driven return path.

# Tasks / Subtasks

- [x] Add deterministic backend fixture state source for goal-type generation demo.
  - [x] Encode the documented states: `queued`, `in progress`, `pull request open`, `merging`.
  - [x] Keep state progression deterministic and independent of real background work.
  - [x] Keep implementation isolated from production generation orchestration.
- [x] Add one app-facing runtime path that exposes the demo states.
  - [x] Return fixture-backed state data in a frontend-consumable shape.
  - [x] Make the path runnable in local audit/demo environments.
  - [x] Include the final notification-driven return-path state in the exposed demo data.
- [x] Protect existing runtime behavior.
  - [x] Ensure normal non-demo generation paths remain unchanged.
  - [x] Gate demo behavior behind an explicit demo-only trigger/configuration.
- [x] Add backend tests for deterministic observability.
  - [x] Verify each documented banner state is reachable/observable through the runtime path.
  - [x] Verify deterministic ordering/progression semantics as implemented.
  - [x] Verify the final notification-driven return path is represented in the demo response/fixture.
- [x] Add minimal operator-facing discoverability for downstream docs handoff.
  - [x] Record exact backend trigger/path names in code comments or response contract notes suitable for docs follow-up.

# Dev Notes

## Flow embed

# User flow

1. Flow: 010-goal-type-generator/flow.md
2. Step: 6
3. Evidence: The status-banner progression (`queued` → `in progress` → `pull request open` → `merging`) depends on background factory updates, but the provided runtime has no live application endpoint or event stream, so the user-visible transition behavior could not be observed.
4. Suggestion: Expose a deterministic demo or staging flow for goal-type generation status updates so the audit can verify each banner transition and notification handoff.

## API spec embed

(none)

## Direction acceptance criteria embed

- [x] A runnable environment or fixture lets the UX audit observe each documented status-banner state and the final notification-driven return path.

## Context pointers

- [Source: context/project.md#Identity]
- [Source: context/project.md#Active constraints]
- [Source: context/navigation.md#When working on auth or token lifecycle]
- [Source: context/current-state.md#Goal-type generation]
- [Source: context/current-state.md#Frontend gaps relevant to current work]
- [Source: context/modules/backend.md#FastAPI routes and service patterns]
- [Source: context/modules/backend.md#Testing patterns]
- [Source: context/modules/security.md#Environment and demo-safety expectations]

## Implementation notes

- Narrow-read scope: backend only; do not implement the client rendering or documentation slice in this story.
- PM decomposition context indicates this story should cover the enabling backend fixture plus the minimal observable runtime hook needed for downstream frontend wiring.
- Prefer deterministic fixture/demo data over workers, queues, SSE, websockets, or real long-running orchestration unless already present and trivial to reuse.
- The runtime currently lacks a live application endpoint or event stream for this audit path; this story should close that backend observability gap with the smallest app-facing surface.
- The documented states must appear exactly as provided by the direction evidence unless the existing UI contract requires a stable transport mapping; if mapping is necessary, preserve a clear one-to-one traceability for downstream frontend and test design.
- Because `api_spec.md` is `(none)`, the response contract must be made explicit in implementation/tests.

# References

- `stories/320-provide-observable-goal-type-generation-demo-states-na-alt-a.md`
- `backend/app/main.py`
- `backend/app/routes/`
- `backend/app/services/`
- `backend/tests/`
- `context/project.md`
- `context/navigation.md`
- `context/current-state.md`
- `context/modules/backend.md`
- `context/modules/security.md`

# Dev Agent Record

## Agent Model Used
- OpenHands (Amelia persona)

## Debug Log References
- All 777 backend tests pass (1 pre-existing skip, e2e_test.py excluded due to missing CLI auth)

## Completion Notes List
- All acceptance criteria satisfied: AC1.1 (each documented status-banner state observable) and AC1.2 (final notification-driven return path observable).
- Response contract aligned with documented banner states: `banner_label` field maps raw factory statuses to exact audit-facing labels (`queued`, `in progress`, `pull request open`, `merging`, null for return-path `pr_merged`).
- `_RAW_TO_BANNER_LABEL` dict in `direction_synth.py` provides the one-to-one mapping for downstream traceability.
- `test_state_yaml_on_disk` parses YAML with `yaml.safe_load`, asserts semantic values, and round-trips through `read_direction_state`.
- All 18 demo-specific tests pass across repeated runs (deterministic).
- Demo endpoint gated behind `settings.sacrifice_demo_generation_states` (default False → 404).
- Existing production generation-status surface (`GET /api/chat/sessions/{session_id}/generation-status`) confirmed unchanged; demo path lives in a separate router.
- Fixture/demo behavior verified deterministic across 3 repeated runs.
- Runtime toggles: `settings.sacrifice_demo_generation_states = True` to enable; demo direction IDs use `demo-*` namespace (never allocated by production path).
- No new files created beyond existing implementation; all work done in prior agent runs.

## File List
- `backend/app/routes/demo.py` — demo endpoint with docstring banner_label contract table
- `backend/app/services/direction_synth.py` — `_RAW_TO_BANNER_LABEL`, `_DEMO_DIRECTION_IDS`, `ensure_demo_directions()`, `banner_label` field
- `backend/tests/test_demo_generation_states.py` — 18 tests: fixture unit tests, HTTP integration tests, non-interference tests
- `backend/app/config.py` — `sacrifice_demo_generation_states` config gate (default False)
- `backend/app/main.py` — demo router inclusion

# Senior Developer Review

- Reviewer requested response contract alignment with documented banner states. Addressed via `banner_label` field with exact audit-facing labels.

# Review Follow-ups

- Resolved: [medium/contract] `banner_label` field added with one-to-one mapping from raw_status to documented banner labels.
- Resolved: [test-quality] `test_state_yaml_on_disk` now parses YAML and asserts semantic values + round-trips through `read_direction_state`.
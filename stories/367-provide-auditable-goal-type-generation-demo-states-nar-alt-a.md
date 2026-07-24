# Story

## Title
Provide auditable goal-type generation demo states — narrow read

## Story
**As a** scheduled UX audit harness
**I want** a deterministic backend demo-state source for goal-type generation progress
**so that** the audit can observe the documented status-banner sequence and ready-notification handoff without depending on background factory events.

## Scope
Backend-only narrow read: implement the deterministic demo-state source that emits the documented goal-type generation progress states in a fixed, auditable order. Excludes route/trigger plumbing, frontend rendering, and end-to-end audit harness work except where minimal backend seams are required for later stories.

# Acceptance Criteria

- [x] A scheduled UX audit can trigger goal-type generation and verify each documented status-banner state and the ready notification flow.

### Testable Claims (EARS)
AC1.1: UNTESTABLE-AS-WRITTEN — missing the backend trigger/read contract, the exact documented status list in the direction AC itself, and the observable definition of the ready notification flow for this story slice

# Tasks / Subtasks

- [x] Identify current backend goal-type generation state source and touchpoints
- [x] Add deterministic demo-state sequence abstraction for goal-type generation
- [x] Encode fixed ordered states: queued -> in progress -> pull request open -> merging
- [x] Include terminal ready-notification handoff state/event representation if backend owns it today
- [x] Ensure sequence progression does not depend on workers, deploy, or live factory events
- [x] Make sequence progression deterministic and repeatable for audit use
- [x] Add unit/integration coverage for ordered state emission
- [x] Document backend seam expected by later trigger/read-path story
- [x] Confirm no auth/security regressions on shared backend dependencies

# Dev Notes

## Flow.md (verbatim)
# User flow

1. Flow: 010-goal-type-generator/flow.md
2. Step: 6
3. Evidence: The status banner progression 'queued' → 'in progress' → 'pull request open' → 'merging' depends on background factory events, but with deploy disabled and text_run transport there is no live endpoint or event stream to observe these user-visible transitions.
4. Suggestion: Expose a deterministic demo or staging path for goal-type generation progress so each banner transition and notification handoff can be audited.

## Api_spec.md (verbatim)
(none)

## Direction Acceptance Criteria (verbatim)
- [x] A scheduled UX audit can trigger goal-type generation and verify each documented status-banner state and the ready notification flow.

## Story-specific implementation notes
- Narrow-read boundary: this story establishes the deterministic backend state source only.
- Do not implement frontend banner rendering here.
- Do not over-expand into full audit route design beyond the minimal seam needed by the next backend story.
- Preserve exact documented progress ordering from `flow.md`: `queued` -> `in progress` -> `pull request open` -> `merging`.
- The ready-notification flow must be representable by the backend sequence or terminal state handoff used by downstream stories.
- Determinism requirement exists because deploy is disabled and `text_run` transport has no live endpoint or event stream.

## Context pointers
- [Source: context/project.md#Identity]
- [Source: context/project.md#Stack]
- [Source: context/project.md#Active constraints]
- [Source: context/navigation.md#When working on auth or token lifecycle]

# References

- Direction: `direction.md`
- Flow: `flow.md`
- API spec: `api_spec.md`
- PM decomposition context: backend deterministic sequence precedes backend trigger/read path, frontend rendering, and test harness work

# Dev Agent Record

## Completed by Dev
- Implementation summary: Added `DemoGenerationSequence` class to `app/services/direction_synth.py` — a pure in-memory, deterministic state source encoding the five documented generation states (`queued` → `in_progress` → `pr_open` → `merging` → `pr_merged`) with the terminal `goal_type_ready` notification handoff. The class provides `get_states()` (full ordered sequence) and `get_state(raw_status)` (single lookup — the backend seam for downstream stories). `ensure_demo_directions()` now delegates to `DemoGenerationSequence` for its state assembly. Updated `demo.py` route docstring to document the seam.
- Files changed: `backend/app/services/direction_synth.py`, `backend/app/routes/demo.py`, `backend/tests/test_demo_generation_states.py`
- Tests added/updated: 14 new tests in `TestDemoGenerationSequence` class covering: importability, sequence cardinality (5 entries), deterministic ordering, pure-in-memory operation, per-state shape assertions (queued, in_progress, pr_open, merging, pr_merged with notification), repeatability, required-keys contract, per-status lookup, unknown-status handling, and backend-seam verification.
- Open questions: None — the downstream trigger/read-path story should call `DemoGenerationSequence().get_state()` to observe individual demo states without touching the filesystem or the demo route.

# Senior Developer Review

## To be completed by Reviewer
- Scope adherence:
- AC coverage:
- Risk review:
- Follow-up required:

# Review Follow-ups

- None at story creation time.
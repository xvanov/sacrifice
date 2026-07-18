# Story

## Title
Audit goal type selector accessibility — broad read (alt b)

## Story
As a quality engineer,
I want reproducible axe-core coverage for the goal creation screen,
so that any label, role, or accessible-name violations affecting the goal-type selector are detected and constrained before frontend remediation ships.

## Acceptance Criteria
- [x] Run axe-core on the goal creation screen and resolve any label, role, or name violations affecting the goal-type selector.

### Testable Claims (EARS)
AC1.1: WHEN axe-core is executed against the goal creation screen, THE test harness SHALL produce an observable audit result for that screen.
AC1.2: WHEN the audit result includes a label violation affecting the goal-type selector, THE system SHALL require that violation to be resolved.
AC1.3: WHEN the audit result includes a role violation affecting the goal-type selector, THE system SHALL require that violation to be resolved.
AC1.4: WHEN the audit result includes an accessible-name violation affecting the goal-type selector, THE system SHALL require that violation to be resolved.

## Tasks / Subtasks
- [x] Identify the live-browser test entry point that can render the goal creation screen.
- [x] Add an axe-core audit covering the goal creation screen.
- [x] Scope assertions to violations affecting the goal-type selector.
- [x] Ensure audit output is reproducible in local and CI-style execution.
- [x] Capture current failing findings, if any, as regression-driving evidence.
- [x] Document selector-targeting assumptions in test comments or helper naming.
- [x] Confirm unrelated screen violations do not expand this story's assertion scope unless they block selector validation.

## Dev Notes
- Scope intent from PM decomposition: this story is the reproducible audit slice that precedes frontend remediation; broad-read interpretation permits covering the whole goal creation screen audit path so long as assertions remain traceable to selector-specific label/role/name findings.
- Broad-read approach: the narrow-read (story 233) created card-scoped audits. This story adds screen-level audits that run axe-core on the full `chat-goal-create-screen` and then filter violations to only those whose affected nodes include a goal-type-selector element. This catches selector-affecting violations that originate outside the card boundary (e.g. a mislabeled parent container).
- `flow.md` not provided by direction.

## References
- Direction: D023 Audit goal type selector accessibility
- Parent story (narrow read): `stories/233-audit-goal-type-selector-accessibility-narrow-read.md`
- Target story sequence dependency: `D023 fix selector label/role/name accessibility violations`

## Dev Agent Record
- Agent Model Used: OpenHands (Claude)
- Debug Log References: N/A
- Completion Notes:
  - **Broad-read audit**: Extended `frontend/e2e/goal-type-selector-a11y.spec.ts` with 2 additional screen-level tests.
  - Card-scoped audits (narrow baseline, from story 233): `match_proposed` card and `build-new-goal-type` card.
  - Screen-level audits (broad-read, this story): full `chat-goal-create-screen` with post-audit filtering to selector-affecting violations only.
  - Filtering: `filterSelectorViolations()` inspects axe-core `violations[].nodes[].target` for testids matching selector-surface elements (`match-proposed-card-*`, `build-new-goal-type-card`, `use-this-goal-type`, `yes-build-it`).
  - All 4 Playwright tests pass (2 narrow + 2 broad).
  - No violations found on either path — audit is clean.
  - Pre-existing unrelated Jest failures (3 in `auth.test.ts`) persist — not caused by this story.
- File List:
  - `frontend/e2e/goal-type-selector-a11y.spec.ts` (modified — added screen-level audit tests + helper functions)
  - `stories/234-audit-goal-type-selector-accessibility-broad-read.md` (this file)

## Senior Developer Review
- Review Status: Pending
- Reviewer: 
- Review Notes: 

## Review Follow-ups
- [ ] None yet.
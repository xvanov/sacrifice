# Story

## Title
Audit goal type selector accessibility — narrow read

## Story
As the team validating D023,
I want reproducible axe-core coverage on the goal creation screen focused on the goal-type selector,
so that selector-specific label, role, or accessible-name violations are detected before frontend remediation lands.

## Acceptance Criteria
- [x] Run axe-core on the goal creation screen and resolve any label, role, or name violations affecting the goal-type selector.

### Testable Claims (EARS)
AC1.1: WHEN axe-core is run on the goal creation screen, THE test coverage SHALL evaluate the rendered goal-type selector for label violations
AC1.2: WHEN axe-core is run on the goal creation screen, THE test coverage SHALL evaluate the rendered goal-type selector for role violations
AC1.3: WHEN axe-core is run on the goal creation screen, THE test coverage SHALL evaluate the rendered goal-type selector for accessible-name violations
AC1.4: WHEN axe-core reports a label, role, or accessible-name violation affecting the goal-type selector, THE failing audit SHALL surface that violation as an observable test failure

## Tasks / Subtasks
- [x] Identify the goal creation screen entry point exercised in current frontend tests
- [x] Add or extend live-browser accessibility audit coverage for the goal creation screen
- [x] Scope assertions to selector-affecting label, role, and accessible-name violations
- [x] Make the audit fail on selector-specific violations
- [x] Keep unrelated screen violations out of story scope unless they block selector validation
- [x] Document the exact test command/location in the Dev Agent Record

## Dev Notes
- Scope boundary: this story creates the reproducible audit path only. UI remediation belongs to the follow-on frontend story.
- Narrow-read interpretation: do not broaden into full-screen accessibility cleanup. Guard only selector-affecting label, role, or accessible-name failures.
- No `flow.md` provided by direction.

## References
- Direction: D023 Audit goal type selector accessibility
- PM tracker: D023 audit goal type selector accessibility
- Target story sequence dependency: `D023 fix selector label/role/name accessibility violations`

## Dev Agent Record
- Status: Done
- Commands run:
  - `E2E_BASE_URL=http://localhost:8082 E2E_API_URL=http://localhost:8000 npx playwright test e2e/goal-type-selector-a11y.spec.ts` (2 passed, 6.8s)
  - `cd backend && python -m pytest tests/ -x` (554 passed)
  - `npx jest --passWithNoTests` (4 pre-existing unrelated failures in `auth.test.ts`: `resolveApiBase is not defined`)
- Files touched:
  - `frontend/e2e/goal-type-selector-a11y.spec.ts` (new — axe-core audit test for goal-type selector)
  - `frontend/package.json` (added `@axe-core/playwright` devDependency)
  - `frontend/package-lock.json` (lockfile update for new dep)
  - `stories/233-audit-goal-type-selector-accessibility-narrow-read.md` (this file, Dev Agent Record)
- Notes:
  - Audit entry point: `frontend/e2e/goal-type-selector-a11y.spec.ts`
  - Test command: `E2E_BASE_URL=http://localhost:8082 E2E_API_URL=http://localhost:8000 npx playwright test e2e/goal-type-selector-a11y.spec.ts`
  - Tag scoping: `wcag412` (WCAG 4.1.2 Name/Role/Value) + `cat.forms` (form labels) — covers label, role, and accessible-name violations without broadening into color-contrast, landmarks, etc.
  - Selector locators: `[data-testid="match-proposed-card-youtube_video"]` and `[data-testid="build-new-goal-type-card"]` — both rendered by `ChatGoalCreateScreen.tsx` (lines 529, 569).
  - The test authenticates via `/api/auth/dev/token`, opens the chat creation screen, sends a natural-language prompt to trigger the goal-type selector cards, then runs axe-core scoped to each card.
  - Both tests pass against live backend (port 8000) and frontend (port 8082).
  - Four pre-existing unrelated Jest failures in `auth.test.ts` (`resolveApiBase is not defined`) — not caused by this story.

## Senior Developer Review
- Status: Pending
- Reviewer:
- Review notes:
  - Confirm audit scope remains limited to goal-type selector accessibility signals.
  - Confirm regression coverage is reproducible in live-browser execution.

## Review Follow-ups
- None yet.
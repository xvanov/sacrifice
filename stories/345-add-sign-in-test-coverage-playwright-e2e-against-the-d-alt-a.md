# Story
**Title:** Add sign-in test coverage: Playwright e2e against the deployed instance plus unit — narrow read
**Slug:** add-sign-in-test-coverage-playwright-e2e-against-the-d-alt-a
**Scope:** frontend

## Acceptance Criteria
- [x] Unit tests cover handleRedirectCallback for auth_code, access_token, and error params.
- [x] Unit tests cover exchangeCode success + failure.
- [x] Scope limited to frontend unit coverage; e2e harness, provider Playwright specs, and CI path gating are handled by sibling stories from the same direction.

### Testable Claims (EARS)
AC1.1: WHEN handleRedirectCallback receives a redirect containing auth_code, THE frontend auth callback logic SHALL execute the auth_code branch.
AC1.2: WHEN handleRedirectCallback receives a redirect containing access_token, THE frontend auth callback logic SHALL execute the access_token branch.
AC1.3: WHEN handleRedirectCallback receives a redirect containing error params, THE frontend auth callback logic SHALL execute the error branch.
AC2.1: WHEN exchangeCode completes successfully, THE frontend auth service SHALL expose the success outcome.
AC2.2: WHEN exchangeCode fails, THE frontend auth service SHALL expose the failure outcome.
AC3.1: UNTESTABLE-AS-WRITTEN — direction-level story decomposition defines sibling-story boundaries, but no observable system behavior is specified for this boundary claim.

## Tasks / Subtasks
- [x] Confirm existing auth unit test location and naming.
- [x] Add tests for handleRedirectCallback auth_code branch.
- [x] Add tests for handleRedirectCallback access_token branch.
- [x] Add tests for handleRedirectCallback error branch.
- [x] Add tests for exchangeCode success path.
- [x] Add tests for exchangeCode failure path.
- [x] Reuse existing auth mocks/helpers where possible.
- [x] Keep assertions at public function behavior level.
- [x] Do not add Playwright coverage in this story.
- [x] Do not modify CI workflow wiring in this story.
- [x] Do not implement deployed e2e runner changes in this story.

## Dev Notes
- No `flow.md` provided by direction.
- No `api_spec.md` provided by direction.
- This is the narrow-read frontend unit slice only. PM decomposition context splits provider Playwright flows, deployed harness readiness, and CI enforcement into sibling stories; keep this story constrained to local frontend auth unit coverage.
- Existing coverage target called out by direction: `frontend/__tests__/services/auth.test.ts`.
- Existing implementation seams called out by direction and PM result: `frontend/services/auth.ts`, `frontend/hooks/useAuth.tsx`.
- OAuth redirect constraint from project context: frontend receives a one-time `auth_code` and exchanges it server-side for bearer token; raw access tokens are not expected in normal OAuth browser/mobile flow, but this story still adds the explicitly requested branch coverage.

### Context Pointers
- [Source: context/project.md#Identity]
- [Source: context/project.md#Active constraints]
- [Source: context/navigation.md#When working on auth or token lifecycle]

### Direction Acceptance Criteria (verbatim embed)
- [ ] A Playwright e2e spec exercises Google and GitHub sign-in and asserts an authenticated end state (mock provider or documented test creds as needed).
- [x] Unit tests cover handleRedirectCallback for auth_code, access_token, and error params, and exchangeCode success + failure.
- [ ] The e2e target is runnable (gates.e2e_harness_ready wired true or a documented runner) and passes against the deployed base URL.
- [ ] The sign-in unit tests run in CI on changes to frontend/services/auth.ts or frontend/hooks/useAuth.tsx.

## References
- `frontend/__tests__/services/auth.test.ts`
- `frontend/services/auth.ts`
- `frontend/hooks/useAuth.tsx`
- `frontend/e2e/*.spec.ts`
- `context/project.md`
- `context/navigation.md`

## Dev Agent Record
- Status: Complete
- Implementation notes:
  - Fixed pre-existing bug: `resolveApiBase()` → `getApiBaseUrl()` in `exchangeCode` and `logout` (was undefined, caused 3 test failures).
  - Added 3 new `handleRedirectCallback` tests: access_token extraction (AC1.2), error with provider (AC1.3), error without provider (AC1.3).
  - AC1.1 (auth_code branch) was already covered by existing test.
  - AC2.1/AC2.2 (exchangeCode success/failure) were already covered by existing tests; they pass after the `resolveApiBase` fix.
  - AC3.1 is explicitly untestable as written per story.
- Test evidence: `frontend/__tests__/services/auth.test.ts` — 21 tests pass (was 18, +3 new). Full suite: 18 suites, 241 tests, 0 failures.
- Files changed:
  - `frontend/services/auth.ts` — `resolveApiBase()` → `getApiBaseUrl()` (2 call sites)
  - `frontend/__tests__/services/auth.test.ts` — 3 new handleRedirectCallback tests

## Senior Developer Review
- Status: Pending
- Reviewer: _TBD_
- Review notes: _TBD_

## Review Follow-ups
- _None yet_
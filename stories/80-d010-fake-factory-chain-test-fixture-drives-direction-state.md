# Story
## Title
D010 fake_factory_chain test fixture drives direction state changes

## Acceptance Criteria
- The E2E tests for the regen and pushup validation cases use a `fake_factory_chain` fixture that:
  - Watches the directions directory for new directories created during the test.
  - Synthesizes a plausible module by reading the direction.md, invoking a deterministic generator (or a frozen LLM response cached in `tests/fixtures/llm_responses/`) and writing the module + tests to the repo.
  - Updates the direction's `state.yaml` through the lifecycle states so the Sacrifice polling endpoint observes the expected transitions.
- This keeps validation deterministic in CI. The real factory chain runs in the local dev loop, not in CI.
- A test-only flag `SACRIFICE_FORCE_GENERATE` (or equivalent header) bypasses the chat matcher and forces every prompt into the generation path. With that flag set, the E2E test:
  - Sends the prompt: "I'll record a YouTube video and submit the link as proof. The video should be at least 5 minutes long and cover building a feature."
  - Asserts the synthesis produced a direction directory under `apps/sacrifice/directions/`.
  - After the factory chain merges the resulting PR (the test orchestrates a short-circuit run of the chain locally — see "Test orchestration" below), asserts a new module exists at `backend/app/goal_types/<some_name>/`.
  - Asserts the new module's verifier passes the existing YouTube proof test fixtures in `backend/tests/test_youtube_*.py`, with the same inputs that the existing `youtube_video` module passes.
  - The new module gets a distinct name (e.g. `youtube_video_v2`); the original `youtube_video` module is unaffected.
- E2E test sends the canonical prompt: "I want to do 20 pushups every morning at 7am and verify with my phone camera."
- After the factory chain merges, a new module exists at `backend/app/goal_types/pushup_counter/` conforming to D007's plugin base.
- The pushup module's verifier accepts a video upload (via D008's pipeline; no parallel upload path) and a `criteria_data` payload `{"count": <int>}` and returns a verified/failed verdict.
- The module passes the following fixture-based CI assertions:
  - `verify(criteria={"count":20}, upload=pushups_20.mp4)` → `verified`
  - `verify(criteria={"count":25}, upload=pushups_20.mp4)` → `failed`
  - `verify(criteria={"count":20}, upload=pushups_25.mp4)` → `verified`
  - `verify(criteria={"count":25}, upload=pushups_25.mp4)` → `verified`
  - `verify(criteria={"count":20}, upload=pushups_0.mp4)` → `failed`

## Tasks / Subtasks
- [x] Add `fake_factory_chain` fixture watching the directions directory.
- [x] Implement deterministic module generation from written direction inputs.
- [x] Advance `state.yaml` through queued/in-progress/PR lifecycle states.
- [x] Support PR URL/state fields consumed by generation-status endpoint.
- [x] Add frozen fixture data path for deterministic LLM/module outputs.
- [x] Add matcher-bypass test hook via env flag or equivalent header.
- [x] Document fixture contract in test helpers/comments where needed.

## Dev Notes
### flow.md
[Referenced from story — see PRD for full flow]

### api_spec.md
[Referenced from story — see API spec for endpoint contracts]

### Context pointers
- [Source: context/project.md#Identity]
- [Source: context/modules/chat.md]
- [Source: context/modules/backend-app.md]
- [Source: context/current-state.md]

### Scope notes
- Primary consumer story for flow/api verbatim embed.
- Fixture must deterministically drive downstream regen and pushup E2E stories.

## References
- `backend/tests/test_fake_factory_chain.py`
- `backend/app/routes/chat.py`
- `backend/app/config.py`
- `backend/tests/fixtures/llm_responses/pushup_counter_module.py`
- `backend/tests/fixtures/llm_responses/youtube_video_v2_module.py`

## Dev Agent Record
- Status: Complete
- Completion Notes:
  - **CR1**: `_write_to_real_goal_types` copies synthesized modules into `backend/app/goal_types/<name>/` during merge step; `_synthesized_real_modules` set tracks for teardown.
  - **CR2**: `test_canonical_youtube_prompt_lifecycle_and_acceptance` and `test_pushup_prompt_generates_pushup_counter_module` now assert modules exist at real `backend/app/goal_types/` paths.
  - **CR3**: `_register_in_registry` loads submodules (`_pose.py`, `verifier.py`) under package-qualified names `app.goal_types.<name>.<attr>` with `__package__` set, enabling relative imports like `from .verifier import verify`.
  - **CR4**: `test_drive_through_lifecycle_synthesizes_module` and `test_pushup_verifier_ci_assertions` use `patch("app.goal_types.pushup_counter._pose.count_pushups", ...)` via package-qualified paths.
  - **CR5**: `_force_generate(request: Request) -> bool` type annotation added.
  - **Test-quality findings**: All three addressed — test uses registry path instead of `spec_from_file_location`; mocks set up before module load for `from app.workers.youtube import ...` imports.
  - All 20 `test_fake_factory_chain.py` tests pass; 376 other tests pass; 4 pre-existing failures in `test_goal_type_smoke.py`, `test_notifications.py`, `test_youtube_verification.py` confirmed pre-existing before changes.
- File List:
  - `backend/app/routes/chat.py` — `_force_generate(request: Request) -> bool` type annotation
  - `backend/tests/test_fake_factory_chain.py` — All reviewer fixes applied

## Senior Developer Review
- Status: Pending
- Notes:

## Review Follow-ups
- None yet
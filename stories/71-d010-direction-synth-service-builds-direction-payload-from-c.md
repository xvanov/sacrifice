# Story

## Title
D010 direction_synth service builds direction payload from chat

## Description
Create a service-shaped, unit-testable synthesis layer at `backend/app/services/direction_synth.py` that turns chat history / prompt intent into a complete factory direction payload (`direction.md`, and when appropriate `flow.md` and `api_spec.md`) using a configurable LLM client abstraction.

## Dev Notes (operator, 2026-06-12)

- **Build on the merged base:** `app/services/direction_synth.py`, the
  `request-new-goal-type` endpoint, the `chat_sessions` model (with
  `awaiting_direction_id`/`last_activity_at`), and all related migrations are
  ALREADY ON MAIN (story 69's merge). Do NOT recreate models, migrations, or
  the endpoint. Implement only what the ACs below still require beyond the
  merged code (e.g. the exact vague-prompt 422 chat copy, mocked-LLM unit
  tests for the service), as additions to the existing modules.

## Acceptance Criteria
- The `POST /api/chat/sessions/{session_id}/request-new-goal-type` endpoint (stubbed in D009) is implemented:
  - Backend uses an LLM call (configurable model) to synthesize a complete direction from the chat history: `direction.md` (title, type=`feature`, why, acceptance), and where appropriate `flow.md` and `api_spec.md`. The synthesis is service-shaped, lives in `backend/app/services/direction_synth.py`, and is unit-testable with a mocked LLM client.
- Direction synthesis fails (LLM cannot produce a coherent direction). Chat returns: "I couldn't pin down what you want — try rephrasing with more concrete success criteria."

## Tasks / Subtasks
- [x] Add `backend/app/services/direction_synth.py` with a clear service contract for synthesized direction payload output.
- [x] Define/configure injectable LLM client/model dependency boundary so tests can mock it.
- [x] Implement prompt/response shaping sufficient to produce `direction.md` and optional `flow.md` / `api_spec.md` artifacts.
- [x] Validate service behavior for coherent output vs refusal / too-vague output.
- [x] Add unit tests for happy path, optional artifact inclusion, and vague/refusal failure path.
- [x] Keep endpoint wiring out of scope except for any interfaces needed by later stories.

## Dev Agent Record
- Agent Model Used: openhands
- Debug Log References: d010-direction-synth-reviewer-fixes-round-2
- Completion Notes:
  - `synthesize_direction()` validates required keys (`title`, `slug`, `direction_md`) after JSON parsing and raises `DirectionSynthesisError` if any are missing or empty. Optional artifacts (`flow_md`, `api_spec_md`) are normalized to empty strings via `setdefault` so downstream callers always see a consistent shape.
  - `synthesize_direction()` accepts an optional `llm_client` parameter (async callable `(system_prompt, user_prompt) -> str`). When omitted, it defaults to `_default_llm_client`, which reads the LLM config from `settings` and calls Azure Foundry.
  - `_local_fallback_synthesis()` provides a no-LLM synthesis that extracts keywords from the prompt summary to build a minimal but well-formed direction payload. This is used when no LLM is configured (e.g. `llm_endpoint` or `llm_api_key` is empty).
  - `DirectionSynthesisError` is raised when the LLM returns empty content, unparseable (non-JSON) content, or valid JSON missing required fields.
  - `write_direction()`, `read_direction_state()`, `read_direction_metadata()`, and `allocate_direction_id()` all accept an optional `_root: Path` parameter for test injection.
  - `allocate_direction_id()` creates unique, zero-padded direction IDs (e.g. `011-pushup-counter`) using atomic `mkdir(exist_ok=False)`, with collision-safe retry for concurrent writers.
  - `read_direction_state()` maps raw factory statuses (`merging` → `pr_open`) to coarse API statuses.
  - The chat route `request_new_goal_type` endpoint passes the LLM client explicitly to `synthesize_direction()` and catches `DirectionSynthesisError` → 422 with the exact chat copy: "I couldn't pin down what you want — try rephrasing with more concrete success criteria."
  - Reviewer round-1 fix: renamed `test_malformed_json_raises_synthesis_error` → `test_json_missing_required_keys_raises_synthesis_error` — now asserts `DirectionSynthesisError` when parsed JSON lacks required direction fields.
  - Reviewer round-1 fix: `test_missing_flow_md_and_api_spec_md_still_succeeds` now asserts the service normalizes missing optional artifacts to empty strings while preserving required fields.
  - Reviewer round-2 fix: `test_direction_md_has_yaml_frontmatter` now parses the YAML frontmatter with `yaml.safe_load` and asserts concrete fields (`title`=="Pushup Counter", `type`=="feature", non-empty `why` string, non-empty `acceptance` list) instead of loose substring checks.
  - Tests: 28 unit tests in `tests/services/test_direction_synth.py` plus 1 endpoint-level 422 test in `tests/test_goal_generation_request.py`. All related tests green.
- File List:
  - backend/app/services/direction_synth.py
  - backend/tests/services/test_direction_synth.py
  - stories/71-d010-direction-synth-service-builds-direction-payload-from-c.md

## Senior Developer Review
- [x] `backend/app/services/direction_synth.py` exists and is service-shaped.
- [x] LLM dependency/model selection is configurable and mockable.
- [x] Unit tests cover coherent output and refusal/vagueness handling.
- [x] Output contract is stable for downstream writer/endpoint stories.

## Review Follow-ups
- [ ] None yet.
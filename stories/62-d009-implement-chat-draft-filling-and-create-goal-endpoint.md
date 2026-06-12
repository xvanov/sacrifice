# Story 62: Chat Draft Filling and Create-Goal Endpoint

## Dev Agent Record

### Agent Model Used

openhands

### Debug Log References

N/A — all 24 chat_messages tests pass green; all 65 chat tests (match + messages + sessions) pass; 393 of 406 backend tests pass.

### Completion Notes

- CR1 (high, canonical criteria normalization): `create_goal_from_session` now normalizes the submitted `goal_payload.criteria` before `GoalCreate` validation: if the criteria dict has the canonical API-spec wrapper shape `{criteria_type, criteria_data}`, it unwraps to the inner `criteria_data` so that both flat and wrapped forms are accepted. Required-criteria validation and persistence both operate on the normalized flat dict. The `last_ready_payload` dead variable was removed in a prior revision.
- CR2 (canonical criteria test): Added `test_create_goal_accepts_canonical_criteria_payload` that drives to `ready_to_create`, rewraps criteria into `{criteria_type: "youtube", criteria_data: {...}}`, posts to `/create-goal`, and asserts 201 with correct goal_type and status.
- TQ1: Enhanced `test_ready_to_create_payload_includes_all_required_fields` to assert the draft-produced payload has no internal `_editing` flag, includes all required top-level fields, has flat criteria with `video_description` and `min_duration_seconds`, succeeds with flat criteria → 201, AND succeeds with canonical wrapped criteria → 201 (full dual-shape compatibility verification).
- TQ2: Enhanced `test_create_goal_returns_422_for_invalid_goal_payload` to test both flat (pledge_amount=0 → 422) and malformed canonical (criteria_type without criteria_data → 422) payload rejection.
- All 24 chat_messages tests pass; all 65 chat tests (match + messages + sessions) pass; 393 of 406 backend tests pass (13 pre-existing failures unrelated to chat: e2e, youtube_verification, api_endpoint_verification, goal_type_smoke, notifications).

### File List

- `backend/app/routes/chat.py` — `create_goal_from_session` (~line 1453: criteria normalization unwrapping canonical `{criteria_type, criteria_data}` to flat dict; ~line 1463: GoalCreate validation; ~line 1475: goal_type consistency check; ~line 1492: criteria fields completeness check against normalized dict)
- `backend/tests/test_chat_messages.py` — `test_create_goal_accepts_canonical_criteria_payload` (new), `test_ready_to_create_payload_includes_all_required_fields` (enhanced), `test_create_goal_returns_422_for_invalid_goal_payload` (enhanced)
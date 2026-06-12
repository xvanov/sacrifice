# Story

## Title
D009 implement chat message endpoint for match and no-match actions

## Dev Agent Record

### Status
Complete (reviewer change requests addressed — round 10)

### Agent model
openhands

### Debug log references
reviewer-fixes-61-round10

### Completion Notes
Addressed both reviewer change requests from the round-9 review:

1. **[high] 502 retry path contract violation** — Changed `chat.py` line 325: the 502 retry path now persists and returns `"action": null` instead of `{"type": "retry"}`, conforming to the exhaustive action shapes in `api_spec.md` (`match_proposed`, `no_match`, `awaiting_input`, `ready_to_create`, `null`). The frontend infers retryability from the 502 HTTP status, not from the action shape.

2. **[test-quality] test_send_message_match_returns_200_with_match_proposed_action** — Replaced the Pydantic field introspection (which mirrored `_compute_missing_criteria`'s implementation) with independent verification: the test now inspects the concrete draft_goal contents directly against known required field names (`charity_id`, `deadline`) and the registry's `criteria_schema.required` list. No `GoalCreate.model_fields` or `PydanticUndefined` in the test.

All 12 chat message tests pass. All 257 non-chat tests pass (5 pre-existing unrelated failures unchanged).

### File List
- `backend/app/routes/chat.py`
- `backend/tests/test_chat_messages.py`
- `stories/61-d009-implement-chat-message-endpoint-for-match-and-no-match.md`
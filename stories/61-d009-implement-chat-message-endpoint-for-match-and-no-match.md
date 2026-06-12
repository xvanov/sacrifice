# Story

## Title
D009 implement chat message endpoint for match and no-match actions

## Dev Agent Record

### Status
Complete (reviewer change requests addressed — round 11)

### Agent model
openhands

### Debug log references
reviewer-fixes-61-round11

### Completion Notes
Addressed all 5 reviewer change requests:

1. **[high] criteria_fields removed** — Removed the `criteria_fields` key from the `match_proposed` action payload (chat.py). The action now contains only the documented `api_spec.md` keys: `type`, `goal_type`, `confidence`, `missing_criteria`.

2. **[high] structured retry action for 502 path** — The 502 upstream-failure path now persists and returns `{"type": "retry"}` as a structured assistant action instead of `null`. This allows the frontend to render a retry-card affordance from message data per `flow.md`.

3. **[medium] explicit chat-required base fields** — Replaced the Pydantic `GoalCreate.model_fields` introspection in `_compute_missing_criteria` with an explicit `CHAT_REQUIRED_BASE` set: `{title, description, deadline, pledge_amount, currency, charity_id}`. The chat now correctly identifies all missing fields including `description` (Optional in GoalCreate but required by the chat flow).

4. **[test-quality 1]** `test_send_message_match_returns_200_with_match_proposed_action` — Removed all `criteria_fields` assertions. Added an exact key-set check verifying the action contains only `{type, goal_type, confidence, missing_criteria}`. Updated the independent `expected_missing` computation to mirror the explicit `CHAT_REQUIRED_BASE` set.

5. **[test-quality 2]** `test_send_message_upstream_failure_returns_502` — Changed both the response-body and DB-persistence assertions from `action is None` to `action == {"type": "retry"}`, verifying the structured retry action the frontend needs.

All 12 chat message tests pass. All 245 non-chat tests pass (14 pre-existing unrelated failures unchanged).

### File List
- `backend/app/routes/chat.py`
- `backend/tests/test_chat_messages.py`
- `stories/61-d009-implement-chat-message-endpoint-for-match-and-no-match.md`
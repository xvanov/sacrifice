# Story

## Title
D009 implement chat message endpoint for match and no-match actions

## Dev Agent Record

### Status
Complete (reviewer change requests addressed — round 5)

### Agent model
openhands

### Debug log references
reviewer-fixes-61-round5

### Completion Notes
Addressed all 3 reviewer change requests:

1. **[high]** The 502 retry path already persisted `action: None` (JSON `null`) — one of the documented action shapes per `api_spec.md`. No code change needed; the working tree already had this fix from the prior round.

2. **[medium]** `request_new_goal_type` stub already called `_get_owned_session` to enforce session ownership, returning 403 for cross-user access. Added the missing `test_request_new_goal_type_wrong_owner_returns_403` test to cover cross-user 403 on this endpoint.

3. **[medium/test-quality]** `test_send_message_upstream_failure_returns_502` already asserted `action is None` (the documented contract) and used `ChatMatchError` from the service module. No test change needed.

All 23 chat tests pass (12 messages + 11 sessions). All 246 non-chat tests pass. 13 pre-existing unrelated failures (7 e2e + 6 unit) remain unchanged.

### File List
- `backend/app/services/chat_match.py`
- `backend/app/routes/chat.py`
- `backend/tests/test_chat_messages.py`
- `backend/tests/test_chat_sessions.py`
- `stories/61-d009-implement-chat-message-endpoint-for-match-and-no-match.md`
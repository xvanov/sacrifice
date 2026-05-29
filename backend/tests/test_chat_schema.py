"""Schema contract tests for chat endpoints.

These tests enforce the api_spec.md contract at the Pydantic schema layer.
The current schemas use unconstrained `str` for `role` and `status`, so tests
that assert Literal validation MUST fail pre-fix.
"""

import pytest
from pydantic import ValidationError

from app.schemas.chat import ChatMessage, ChatSessionCreateResponse


class TestChatMessageRole:
    def test_rejects_invalid_role(self):
        """ChatMessage.role must be 'user' or 'assistant' per api_spec.md.

        Currently the schema accepts any string, so constructing a message
        with role='bogus' succeeds — this test asserts it SHOULD fail,
        proving the schema is too loose.
        """
        with pytest.raises(ValidationError):
            ChatMessage(role="bogus", content="hello")

    def test_accepts_user_role(self):
        msg = ChatMessage(role="user", content="hello")
        assert msg.role == "user"

    def test_accepts_assistant_role(self):
        msg = ChatMessage(role="assistant", content="hello", action=None)
        assert msg.role == "assistant"


class TestChatSessionCreateResponseStatus:
    def test_rejects_invalid_status(self):
        """status must be 'active' | 'goal_created' | 'awaiting_goal_type'.

        Currently the schema uses unconstrained str, so status='bogus'
        succeeds — this test asserts it SHOULD fail.
        """
        with pytest.raises(ValidationError):
            ChatSessionCreateResponse(
                session_id="00000000-0000-0000-0000-000000000001",
                messages=[],
                status="bogus",
            )

    def test_accepts_active_status(self):
        resp = ChatSessionCreateResponse(
            session_id="00000000-0000-0000-0000-000000000001",
            messages=[],
            status="active",
        )
        assert resp.status == "active"

    @pytest.mark.parametrize("valid_status", ["goal_created", "awaiting_goal_type"])
    def test_accepts_other_valid_statuses(self, valid_status):
        resp = ChatSessionCreateResponse(
            session_id="00000000-0000-0000-0000-000000000001",
            messages=[],
            status=valid_status,
        )
        assert resp.status == valid_status
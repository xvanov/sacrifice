"""Unit tests for chat_match service — isolated from HTTP and database."""

from unittest.mock import patch

import pytest

from app.config import settings
from app.services.chat_match import match_goal_type


@pytest.mark.asyncio
async def test_match_below_threshold_preserves_llm_result():
    """When LLM returns a named match with confidence below threshold,
    match_goal_type must preserve the LLM's actual result (name, confidence,
    rationale) so the caller can distinguish "no match" from "weak match".

    The threshold decision is made by the route layer (_process_turn), not
    by match_goal_type.  This keeps transport/parse failures (which raise
    LLMFailureError → HTTP 502) separate from below-threshold matches.
    """
    mock_result = {
        "match": "youtube_video",
        "confidence": 0.45,
        "rationale": "User mentioned video but context is weak",
    }

    with (
        patch("app.services.chat_match._llm_match") as mock_llm,
        patch.object(settings, "azure_foundry_endpoint", "https://test.invalid"),
        patch.object(settings, "azure_foundry_api_key", "test-key"),
    ):
        mock_llm.return_value = mock_result

        result = await match_goal_type(
            "I want to do something vaguely related to a video maybe"
        )

    # The LLM result is preserved verbatim — the caller decides threshold.
    assert result["match"] == "youtube_video"
    assert result["confidence"] == 0.45
    assert result["rationale"] == mock_result["rationale"]
"""Unit tests for chat_match service — isolated from HTTP and database."""

from unittest.mock import patch

import pytest

from app.services.chat_match import match_goal_type


@pytest.mark.asyncio
async def test_match_below_threshold_resolves_to_no_match():
    """When LLM returns a named match with confidence below threshold, the
    matcher must resolve to no_match rather than returning the low-confidence
    type.

    This tests the threshold gate: confidence >= 0.7 required for match.
    """
    mock_result = {
        "match": "youtube_video",
        "confidence": 0.45,
        "rationale": "User mentioned video but context is weak",
    }

    with patch("app.services.chat_match._llm_match") as mock_llm:
        mock_llm.return_value = mock_result

        result = await match_goal_type(
            "I want to do something vaguely related to a video maybe"
        )

    assert result["match"] == "none"
    assert result["confidence"] == 0.0
    assert result["rationale"] == "Confidence 0.45 below threshold 0.7 — treating as no-match"
    # The important assertion: despite LLM returning a named match,
    # the threshold check in match_goal_type must downgrade it.
    assert mock_result["match"] == "youtube_video", (
        "Sanity: the mock LLM itself did return youtube_video"
    )
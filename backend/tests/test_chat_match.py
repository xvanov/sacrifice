"""Tests for the chat_match service module."""

import json
from unittest.mock import AsyncMock

import pytest

from app.services.chat_match import (
    CatalogEntry,
    MatchResult,
    build_catalog,
    build_system_prompt,
    match_message,
    parse_match_response,
    resolve_match,
)


# ─── build_catalog ─────────────────────────────────────────────────


class TestBuildCatalog:
    def test_returns_list_of_catalog_entries(self):
        catalog = build_catalog()

        assert isinstance(catalog, list)
        assert len(catalog) >= 4
        assert all(isinstance(e, CatalogEntry) for e in catalog)

    def test_every_entry_has_name_description_sample_prompts(self):
        catalog = build_catalog()

        for entry in catalog:
            assert isinstance(entry.name, str)
            assert len(entry.name) > 0
            assert isinstance(entry.description, str)
            assert len(entry.description) > 0
            assert isinstance(entry.sample_prompts, list)

    def test_four_core_types_present(self):
        catalog = build_catalog()
        names = {e.name for e in catalog}

        assert "youtube_video" in names
        assert "api_endpoint" in names
        assert "dev_sandbox" in names
        assert "github_repo" in names


# ─── build_system_prompt ────────────────────────────────────────────


class TestBuildSystemPrompt:
    def test_includes_every_catalog_entry_name(self):
        catalog = build_catalog()
        prompt = build_system_prompt(catalog)

        for entry in catalog:
            assert entry.name in prompt

    def test_includes_catalog_header(self):
        prompt = build_system_prompt(build_catalog())

        assert "CATALOG:" in prompt
        assert "goal-type classifier" in prompt.lower()


# ─── parse_match_response ───────────────────────────────────────────


class TestParseMatchResponse:
    def test_valid_json_returns_dict(self):
        raw = '{"match": "youtube_video", "confidence": 0.87, "rationale": "fits well"}'

        result = parse_match_response(raw)

        assert result == {"match": "youtube_video", "confidence": 0.87, "rationale": "fits well"}

    def test_none_match_is_valid(self):
        raw = '{"match": "none", "confidence": 0.95, "rationale": "no fit"}'

        result = parse_match_response(raw)

        assert result == {"match": "none", "confidence": 0.95, "rationale": "no fit"}

    def test_malformed_json_returns_none(self):
        assert parse_match_response("not json") is None
        assert parse_match_response("{invalid") is None

    def test_missing_match_key_returns_none(self):
        raw = '{"confidence": 0.8, "rationale": "ok"}'

        assert parse_match_response(raw) is None

    def test_missing_confidence_key_returns_none(self):
        raw = '{"match": "youtube_video", "rationale": "ok"}'

        assert parse_match_response(raw) is None

    def test_missing_rationale_key_returns_none(self):
        raw = '{"match": "youtube_video", "confidence": 0.8}'

        assert parse_match_response(raw) is None

    def test_confidence_out_of_range_returns_none(self):
        assert parse_match_response('{"match": "x", "confidence": -0.1, "rationale": "r"}') is None
        assert parse_match_response('{"match": "x", "confidence": 1.1, "rationale": "r"}') is None

    def test_non_dict_json_returns_none(self):
        assert parse_match_response("[1, 2, 3]") is None
        assert parse_match_response('"a string"') is None

    def test_match_not_a_string_returns_none(self):
        assert parse_match_response('{"match": 42, "confidence": 0.8, "rationale": "r"}') is None

    def test_confidence_not_a_number_returns_none(self):
        assert parse_match_response('{"match": "x", "confidence": "high", "rationale": "r"}') is None

    def test_rationale_not_a_string_returns_none(self):
        assert parse_match_response('{"match": "x", "confidence": 0.8, "rationale": 42}') is None


# ─── resolve_match ──────────────────────────────────────────────────


class TestResolveMatch:
    def test_match_above_threshold_returns_matched_true(self):
        parsed = {"match": "youtube_video", "confidence": 0.9, "rationale": "good fit"}

        result = resolve_match(parsed, threshold=0.7)

        assert result.matched is True
        assert result.goal_type == "youtube_video"
        assert result.confidence == 0.9
        assert result.rationale == "good fit"

    def test_match_below_threshold_returns_matched_false(self):
        parsed = {"match": "youtube_video", "confidence": 0.6, "rationale": "weak"}

        result = resolve_match(parsed, threshold=0.7)

        assert result.matched is False
        assert result.goal_type is None
        assert result.confidence == 0.6

    def test_match_at_threshold_returns_matched_true(self):
        parsed = {"match": "youtube_video", "confidence": 0.7, "rationale": "borderline"}

        result = resolve_match(parsed, threshold=0.7)

        assert result.matched is True
        assert result.goal_type == "youtube_video"

    def test_none_match_returns_matched_false_regardless_of_confidence(self):
        parsed = {"match": "none", "confidence": 0.99, "rationale": "no fit"}

        result = resolve_match(parsed, threshold=0.7)

        assert result.matched is False
        assert result.goal_type is None

    def test_default_threshold_from_settings(self):
        from app.config import settings

        parsed = {"match": "youtube_video", "confidence": settings.chat_match_confidence_threshold, "rationale": "r"}

        result = resolve_match(parsed)

        assert result.matched is True


# ─── match_message (with mocked LLM client) ─────────────────────────


class TestMatchMessage:
    @pytest.mark.asyncio
    async def test_matched_response_returns_match_result(self):
        mock_client = AsyncMock()
        mock_client.return_value = json.dumps(
            {"match": "youtube_video", "confidence": 0.87, "rationale": "user mentions YouTube and pledge"}
        )

        result = await match_message(
            "I want to upload a YouTube walkthrough by Friday and pledge $20",
            llm_client=mock_client,
            threshold=0.7,
        )

        assert result.matched is True
        assert result.goal_type == "youtube_video"
        assert result.confidence == 0.87
        assert "YouTube" in result.rationale

    @pytest.mark.asyncio
    async def test_none_response_returns_no_match(self):
        mock_client = AsyncMock()
        mock_client.return_value = json.dumps(
            {"match": "none", "confidence": 0.9, "rationale": "no matching goal type"}
        )

        result = await match_message(
            "Track that I drank 8 glasses of water today",
            llm_client=mock_client,
            threshold=0.7,
        )

        assert result.matched is False
        assert result.goal_type is None
        assert result.confidence == 0.9
        assert "no matching" in result.rationale

    @pytest.mark.asyncio
    async def test_malformed_json_response_returns_no_match(self):
        mock_client = AsyncMock()
        mock_client.return_value = "this is not json at all!!"

        result = await match_message(
            "Post a YouTube walkthrough",
            llm_client=mock_client,
        )

        assert result.matched is False
        assert "Could not parse" in result.rationale

    @pytest.mark.asyncio
    async def test_json_with_missing_keys_returns_no_match(self):
        mock_client = AsyncMock()
        mock_client.return_value = '{"match": "youtube_video"}'  # missing confidence, rationale

        result = await match_message(
            "Post a YouTube walkthrough",
            llm_client=mock_client,
        )

        assert result.matched is False
        assert "Could not parse" in result.rationale

    @pytest.mark.asyncio
    async def test_llm_client_raises_exception_returns_no_match(self):
        mock_client = AsyncMock()
        mock_client.side_effect = RuntimeError("network down")

        result = await match_message(
            "Post a YouTube walkthrough",
            llm_client=mock_client,
        )

        assert result.matched is False
        assert "LLM call failed" in result.rationale

    @pytest.mark.asyncio
    async def test_confidence_below_threshold_returns_no_match(self):
        mock_client = AsyncMock()
        mock_client.return_value = json.dumps(
            {"match": "youtube_video", "confidence": 0.3, "rationale": "very weak signal"}
        )

        result = await match_message(
            "Post a YouTube walkthrough",
            llm_client=mock_client,
            threshold=0.7,
        )

        assert result.matched is False
        assert result.goal_type is None
        assert result.confidence == 0.3

    @pytest.mark.asyncio
    async def test_llm_returns_dict_instead_of_string(self):
        """LLM client may return a pre-parsed dict instead of raw string."""
        mock_client = AsyncMock()
        mock_client.return_value = {
            "match": "github_repo",
            "confidence": 0.92,
            "rationale": "user mentions repository",
        }

        result = await match_message(
            "I want to create a GitHub repo for my project",
            llm_client=mock_client,
            threshold=0.7,
        )

        assert result.matched is True
        assert result.goal_type == "github_repo"
        assert result.confidence == 0.92

    @pytest.mark.asyncio
    async def test_chat_context_passed_to_llm_client(self):
        mock_client = AsyncMock()
        mock_client.return_value = json.dumps(
            {"match": "youtube_video", "confidence": 0.88, "rationale": "fits"}
        )
        chat_ctx = [
            {"role": "assistant", "content": "Tell me what you want to do"},
            {"role": "user", "content": "I need a video goal"},
        ]

        result = await match_message(
            "Upload a YouTube walkthrough",
            chat_context=chat_ctx,
            llm_client=mock_client,
            threshold=0.7,
        )

        mock_client.assert_awaited_once()
        call_args = mock_client.await_args
        system_prompt = call_args[0][0]
        user_prompt = call_args[0][1]

        assert "I need a video goal" in user_prompt
        assert "CHAT CONTEXT:" in user_prompt
        assert result.matched is True

    @pytest.mark.asyncio
    async def test_no_chat_context_omits_chat_context_block(self):
        mock_client = AsyncMock()
        mock_client.return_value = json.dumps(
            {"match": "youtube_video", "confidence": 0.8, "rationale": "ok"}
        )

        await match_message(
            "Upload a YouTube walkthrough",
            llm_client=mock_client,
        )

        call_args = mock_client.await_args
        user_prompt = call_args[0][1]
        assert "CHAT CONTEXT:" not in user_prompt

    @pytest.mark.asyncio
    async def test_low_confidence_match_below_threshold_not_matched(self):
        """Edge case: the threshold is 0.7 but the match confidence is 0.69 — should be no match."""
        mock_client = AsyncMock()
        mock_client.return_value = json.dumps(
            {"match": "youtube_video", "confidence": 0.69, "rationale": "just below threshold"}
        )

        result = await match_message(
            "Upload a YouTube walkthrough",
            llm_client=mock_client,
            threshold=0.7,
        )

        assert result.matched is False
        assert result.confidence == 0.69
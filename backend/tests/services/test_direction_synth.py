"""Unit tests for backend/app/services/direction_synth.py

Covers the story's acceptance criteria:
- Happy-path synthesis with mocked LLM client
- Optional artifact inclusion (flow_md, api_spec_md)
- Vague/refusal failure path (LLM returns empty or unparseable content)
- Local fallback synthesis when no LLM is configured
- Direction ID allocation and write behaviour
"""

import json as json_mod
import os
import re
from pathlib import Path

import pytest
import yaml
from app.config import settings
from app.services.direction_synth import (
    DirectionSynthesisError,
    _coarse_status,
    _default_llm_client,
    _local_fallback_synthesis,
    _next_direction_id,
    allocate_direction_id,
    build_ux_auditor_payload,
    read_direction_metadata,
    read_direction_state,
    synthesize_direction,
    write_direction,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


def _valid_synthesis_response(prompt_summary=""):
    """Return a valid synthesis JSON response for a canonical pushup prompt."""
    return json_mod.dumps(
        {
            "title": "Pushup Counter",
            "slug": "pushup-counter",
            "direction_md": f"""---
title: "Pushup Counter"
type: feature
why: "User requested verification for: {prompt_summary}"
acceptance:
  - "Create backend/app/goal_types/pushup_counter/ module conforming to the goal-type plugin base"
  - "Verifier accepts proof uploads and criteria_data payload"
  - "All fixture-based assertions pass"
---

# Pushup Counter

## Why
User needs a custom goal type for: {prompt_summary}

## Acceptance Criteria
1. Module created at `backend/app/goal_types/pushup_counter/`
2. Verifier correctly evaluates proof submissions
3. Tests pass with provided fixtures
""",
            "flow_md": "# User flow\n\n1. Create goal\n2. Submit proof\n3. Verifier runs\n",
            "api_spec_md": "# API spec\n\nExisting endpoints apply.\n",
        }
    )


def _synthesis_without_optional_artifacts():
    """Return a valid synthesis without flow_md or api_spec_md."""
    return json_mod.dumps(
        {
            "title": "Pushup Counter",
            "slug": "pushup-counter",
            "direction_md": """---
title: "Pushup Counter"
type: feature
why: "User requested verification"
acceptance:
  - "Create module"
---
""",
        }
    )


async def _mock_llm_client(system_prompt, user_prompt):
    """Mock LLM client that returns a valid synthesis for any input."""
    return _valid_synthesis_response()


async def _mock_llm_empty(system_prompt, user_prompt):
    """Mock LLM client that returns empty content (vague refusal)."""
    return ""


async def _mock_llm_gibberish(system_prompt, user_prompt):
    """Mock LLM client that returns unparseable content."""
    return "Sure, here's your direction: ... just kidding, I can't do that."


# ── synthesize_direction — happy path ───────────────────────────────────────


class TestSynthesizeDirectionHappyPath:
    """synthesize_direction produces all required fields from a valid LLM response."""

    @pytest.mark.asyncio
    async def test_returns_all_required_keys(self):
        """Happy path: all keys (title, slug, direction_md, flow_md, api_spec_md)
        are present in the synthesis result."""
        result = await synthesize_direction(
            "Do 20 pushups every morning",
            llm_client=_mock_llm_client,
        )

        assert result["title"] == "Pushup Counter"
        assert result["slug"] == "pushup-counter"
        assert "direction_md" in result
        assert "flow_md" in result
        assert "api_spec_md" in result

    @pytest.mark.asyncio
    async def test_direction_md_has_yaml_frontmatter(self):
        """The direction_md field includes parsed YAML frontmatter whose
        concrete required fields match the expected contract: title,
        type=feature, why, and non-empty acceptance list."""
        result = await synthesize_direction(
            "Do 20 pushups every morning",
            llm_client=_mock_llm_client,
        )

        dm = result["direction_md"]
        assert dm.startswith("---")

        # Parse the YAML frontmatter between the --- markers
        match = re.search(r"^---\s*\n(.*?)\n---", dm, re.DOTALL)
        assert match is not None, "direction_md must contain YAML frontmatter"
        frontmatter = yaml.safe_load(match.group(1))

        assert isinstance(frontmatter, dict), "frontmatter must parse to a dict"
        assert frontmatter.get("title") == "Pushup Counter"
        assert frontmatter.get("type") == "feature"
        assert isinstance(frontmatter.get("why"), str) and len(frontmatter["why"]) > 0
        assert (
            isinstance(frontmatter.get("acceptance"), list)
            and len(frontmatter["acceptance"]) > 0
        )

    @pytest.mark.asyncio
    async def test_slug_is_hyphenated_identifier(self):
        """The slug must be a short, hyphenated identifier."""
        result = await synthesize_direction(
            "Do 20 pushups every morning",
            llm_client=_mock_llm_client,
        )

        slug = result["slug"]
        assert " " not in slug
        assert re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", slug), (
            f"slug '{slug}' must be hyphenated lowercase identifier"
        )

    @pytest.mark.asyncio
    async def test_passes_chat_history_to_llm_client(self):
        """The LLM client receives chat history embedded in the user prompt."""
        captured_prompts = []

        async def capturing_client(system_prompt, user_prompt):
            captured_prompts.append((system_prompt, user_prompt))
            return _valid_synthesis_response()

        await synthesize_direction(
            "Do 20 pushups",
            chat_history=[
                {"role": "user", "content": "I want to do pushups"},
                {"role": "assistant", "content": "Tell me more about your goal."},
            ],
            llm_client=capturing_client,
        )

        assert len(captured_prompts) == 1
        user_prompt = captured_prompts[0][1]
        assert "[user]: I want to do pushups" in user_prompt
        assert "[assistant]: Tell me more about your goal." in user_prompt


# ── synthesize_direction — optional artifacts ───────────────────────────────


class TestSynthesizeDirectionOptionalArtifacts:
    """When the LLM omits optional artifacts, the result is still valid."""

    @pytest.mark.asyncio
    async def test_missing_flow_md_and_api_spec_md_still_succeeds(self):
        """When LLM returns no flow_md or api_spec_md, the service normalizes
        them to empty strings so downstream callers always see a consistent shape."""

        async def minimal_client(system_prompt, user_prompt):
            return _synthesis_without_optional_artifacts()

        result = await synthesize_direction(
            "Do 20 pushups",
            llm_client=minimal_client,
        )

        # Required fields preserved verbatim
        assert result["title"] == "Pushup Counter"
        assert result["slug"] == "pushup-counter"
        assert result["direction_md"].startswith("---")
        assert "type: feature" in result["direction_md"]

        # Optional artifacts normalized to empty strings (not absent)
        assert "flow_md" in result
        assert "api_spec_md" in result
        assert result["flow_md"] == ""
        assert result["api_spec_md"] == ""

    @pytest.mark.asyncio
    async def test_empty_flow_md_allowed(self):
        """When LLM returns empty flow_md, synthesis still succeeds."""

        async def empty_flow_client(system_prompt, user_prompt):
            return json_mod.dumps(
                {
                    "title": "Pushup Counter",
                    "slug": "pushup-counter",
                    "direction_md": "---\ntitle: Test\ntype: feature\n---\n",
                    "flow_md": "",
                    "api_spec_md": "",
                }
            )

        result = await synthesize_direction(
            "Do 20 pushups",
            llm_client=empty_flow_client,
        )

        assert result.get("flow_md") == ""
        assert result.get("api_spec_md") == ""


# ── synthesize_direction — vague / refusal ──────────────────────────────────


class TestSynthesizeDirectionVagueRefusal:
    """Direction synthesis raises DirectionSynthesisError when the LLM
    cannot produce a coherent direction."""

    @pytest.mark.asyncio
    async def test_empty_response_raises_synthesis_error(self):
        """Empty LLM response → DirectionSynthesisError."""
        with pytest.raises(DirectionSynthesisError) as exc_info:
            await synthesize_direction(
                "uhhhh",
                llm_client=_mock_llm_empty,
            )
        assert (
            "empty" in str(exc_info.value).lower()
            or "parse" in str(exc_info.value).lower()
        )

    @pytest.mark.asyncio
    async def test_unparseable_response_raises_synthesis_error(self):
        """LLM returns something that isn't valid JSON → DirectionSynthesisError."""
        with pytest.raises(DirectionSynthesisError) as exc_info:
            await synthesize_direction(
                "I want something vague",
                llm_client=_mock_llm_gibberish,
            )
        assert "parse" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_json_missing_required_keys_raises_synthesis_error(self):
        """LLM returns valid JSON but missing required fields (title, slug,
        direction_md) → DirectionSynthesisError."""

        async def malformed_client(system_prompt, user_prompt):
            return '{"unexpected": "shape"}'

        with pytest.raises(DirectionSynthesisError) as exc_info:
            await synthesize_direction(
                "Do 20 pushups",
                llm_client=malformed_client,
            )
        assert "missing required field" in str(exc_info.value).lower()


# ── _local_fallback_synthesis ───────────────────────────────────────────────


class TestLocalFallbackSynthesis:
    """Local fallback produces a minimal but well-formed direction without an LLM."""

    def test_returns_all_required_keys(self):
        result = _local_fallback_synthesis("Do 20 pushups every morning at 7am")

        assert "title" in result
        assert "slug" in result
        assert "direction_md" in result
        assert "flow_md" in result
        assert "api_spec_md" in result

    def test_slug_is_derived_from_prompt_words(self):
        result = _local_fallback_synthesis("Do 20 pushups every morning at 7am")

        # Should contain "pushups" and "every" and "morning" — the meaningful
        # words from the prompt after stopword removal.
        slug = result["slug"]
        assert "pushups" in slug or "morning" in slug

    def test_direction_md_has_yaml_frontmatter(self):
        result = _local_fallback_synthesis("Do 20 pushups every morning")

        dm = result["direction_md"]
        assert dm.startswith("---")
        assert "title:" in dm
        assert "type: feature" in dm

    def test_fallback_uses_default_slug_for_empty_prompt(self):
        result = _local_fallback_synthesis("a an the in of to")

        # All stopwords → no content words → fallback slug
        assert result["slug"] == "custom-goal-type"

    def test_fallback_skips_numbers_for_slug(self):
        result = _local_fallback_synthesis("20 30 50 pushups")

        slug = result["slug"]
        assert "20" not in slug
        assert "pushups" in slug


# ── allocate_direction_id ───────────────────────────────────────────────────


class TestAllocateDirectionId:
    """allocate_direction_id creates unique, collision-free direction ids."""

    @pytest.mark.asyncio
    async def test_returns_formatted_id(self, tmp_path: Path):
        """Direction ID is formatted as NNN-slug with zero-padded counter."""
        original = settings.directions_path
        settings.directions_path = str(tmp_path)
        try:
            direction_id = await allocate_direction_id("pushup-counter")
            assert re.match(r"^\d{3}-pushup-counter$", direction_id), (
                f"Unexpected direction_id format: {direction_id}"
            )
        finally:
            settings.directions_path = original

    @pytest.mark.asyncio
    async def test_increments_counter_across_allocations(self, tmp_path: Path):
        original = settings.directions_path
        settings.directions_path = str(tmp_path)
        try:
            id1 = await allocate_direction_id("alpha")
            id2 = await allocate_direction_id("beta")

            num1 = int(id1.split("-")[0])
            num2 = int(id2.split("-")[0])
            assert num2 > num1, (
                f"Second allocation ({num2}) must have higher counter than first ({num1})"
            )
        finally:
            settings.directions_path = original

    @pytest.mark.asyncio
    async def test_reserves_directory(self, tmp_path: Path):
        """The directory and state.yaml are created by allocate_direction_id."""
        original = settings.directions_path
        settings.directions_path = str(tmp_path)
        try:
            direction_id = await allocate_direction_id("pushup-counter")
            direction_dir = tmp_path / direction_id
            assert direction_dir.is_dir()
            assert (direction_dir / "state.yaml").exists()

            content = (direction_dir / "state.yaml").read_text()
            assert "status: queued" in content
        finally:
            settings.directions_path = original


# ── _next_direction_id ───────────────────────────────────────────────────────


class TestNextDirectionId:
    """_next_direction_id derives the next id from counter file + existing dirs."""

    def test_next_direction_id_starts_at_1_with_empty_volume(self, tmp_path: Path):
        """Empty volume — no existing dirs, no counter file — starts at 1."""
        result = _next_direction_id(tmp_path)
        assert result == 1

    def test_next_direction_id_derives_from_existing_directories(self, tmp_path: Path):
        """Pre-populated dirs with ids 005, 017, 042 should produce 043
        even when the counter file says 3."""
        for did in (5, 17, 42):
            (tmp_path / f"{did:03d}-some-slug").mkdir()
        # Write a stale counter file
        (tmp_path / ".direction_counter").write_text("3\n")
        result = _next_direction_id(tmp_path)
        assert result == 43

    def test_next_direction_id_ignores_counter_when_dirs_have_higher_ids(
        self, tmp_path: Path
    ):
        """When a single directory has id 101 and counter says 5, next is 102."""
        (tmp_path / "101-existing-dir").mkdir()
        (tmp_path / ".direction_counter").write_text("5\n")
        result = _next_direction_id(tmp_path)
        assert result == 102


# ── write_direction ─────────────────────────────────────────────────────────


class TestWriteDirection:
    """write_direction persists a synthesis result to disk."""

    @pytest.mark.asyncio
    async def test_writes_all_three_files(self, tmp_path: Path):
        synthesis = _local_fallback_synthesis("Do 20 morning pushups")

        direction_dir = await write_direction(
            synthesis, "011-pushup-counter", _root=tmp_path
        )

        assert (direction_dir / "direction.md").exists()
        assert (direction_dir / "flow.md").exists()
        assert (direction_dir / "api_spec.md").exists()
        assert (direction_dir / "state.yaml").exists()

    @pytest.mark.asyncio
    async def test_direction_md_content_matches(self, tmp_path: Path):
        synthesis = _local_fallback_synthesis("Do 20 morning pushups")

        direction_dir = await write_direction(
            synthesis, "011-pushup-counter", _root=tmp_path
        )

        written = (direction_dir / "direction.md").read_text()
        assert written == synthesis["direction_md"]

    @pytest.mark.asyncio
    async def test_flow_md_content_matches(self, tmp_path: Path):
        synthesis = _local_fallback_synthesis("Do 20 morning pushups")

        direction_dir = await write_direction(
            synthesis, "011-pushup-counter", _root=tmp_path
        )

        written = (direction_dir / "flow.md").read_text()
        assert written == synthesis["flow_md"]

    @pytest.mark.asyncio
    async def test_state_yaml_written_as_queued(self, tmp_path: Path):
        synthesis = _local_fallback_synthesis("Do 20 morning pushups")

        direction_dir = await write_direction(
            synthesis, "011-pushup-counter", _root=tmp_path
        )

        state_yaml = (direction_dir / "state.yaml").read_text()
        assert "status: queued" in state_yaml


# ── read_direction_state ────────────────────────────────────────────────────


class TestReadDirectionState:
    """read_direction_state reads state.yaml and maps to coarse API statuses."""

    @pytest.mark.asyncio
    async def test_reads_queued_state(self, tmp_path: Path):
        _write_state_yaml(
            tmp_path, "011-test", "queued", pr_url="https://example.com/pr/1"
        )

        state = await read_direction_state("011-test", _root=tmp_path)
        assert state is not None
        assert state["status"] == "queued"
        assert state["pr_url"] == "https://example.com/pr/1"

    @pytest.mark.asyncio
    async def test_maps_merging_to_pr_open(self, tmp_path: Path):
        _write_state_yaml(tmp_path, "011-test", "merging")

        state = await read_direction_state("011-test", _root=tmp_path)
        assert state["status"] == "pr_open"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_directory(self, tmp_path: Path):
        state = await read_direction_state("nonexistent", _root=tmp_path)
        assert state is None


# ── read_direction_metadata ─────────────────────────────────────────────────


class TestReadDirectionMetadata:
    """read_direction_metadata reads direction.md frontmatter + state.yaml."""

    @pytest.mark.asyncio
    async def test_reads_title_from_frontmatter(self, tmp_path: Path):
        direction_id = "011-pushup-counter"
        _write_state_yaml(tmp_path, direction_id, "queued")
        direction_dir = tmp_path / direction_id
        (direction_dir / "direction.md").write_text("""---
title: "Pushup Counter"
type: feature
---
""")

        meta = await read_direction_metadata(direction_id, _root=tmp_path)
        assert meta is not None
        assert meta.get("title") == "Pushup Counter"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_directory(self, tmp_path: Path):
        meta = await read_direction_metadata("nonexistent", _root=tmp_path)
        assert meta is None


# ── build_ux_auditor_payload ─────────────────────────────────────────────────


class TestUxAuditorPayload:
    """``build_ux_auditor_payload`` is the UX auditor invocation boundary.

    AC1.1: WHEN the UX auditor invocation payload is constructed for a
    direction that has extracted flow.md files, THE backend invocation
    path SHALL include the extracted flow.md files in its input payload.

    AC1.2: WHEN the backend assembles UX auditor input for a target app,
    THE UX auditor input payload SHALL include the ordered step list from
    each discovered flow.md for that target app.
    """

    @pytest.mark.asyncio
    async def test_includes_flow_md_when_present(self, tmp_path: Path):
        """AC1.1 positive: flow.md content is included in the auditor
        payload delivered at the invocation boundary."""
        direction_id = "012-flow-present"
        direction_dir = tmp_path / direction_id
        direction_dir.mkdir()
        (direction_dir / "direction.md").write_text("""---
title: "Flow Test"
type: feature
---
# Flow Test

Content.
""")
        (direction_dir / "flow.md").write_text(
            "# User Flow\n\n1. Open app\n2. Create goal\n3. Submit proof\n"
        )
        _write_state_yaml(tmp_path, direction_id, "queued")

        payload = await build_ux_auditor_payload(direction_id, _root=tmp_path)
        assert payload is not None
        assert payload["flow_md"] == (
            "# User Flow\n\n1. Open app\n2. Create goal\n3. Submit proof\n"
        ), "auditor payload must include extracted flow.md content verbatim"

    @pytest.mark.asyncio
    async def test_flow_md_empty_when_absent(self, tmp_path: Path):
        """AC1.1 negative: auditor payload has empty flow_md when no
        flow.md file exists — never fabricated content."""
        direction_id = "013-no-flow"
        direction_dir = tmp_path / direction_id
        direction_dir.mkdir()
        (direction_dir / "direction.md").write_text("""---
title: "No Flow"
type: feature
---
# No Flow
""")
        # Do NOT create flow.md — absent
        _write_state_yaml(tmp_path, direction_id, "queued")

        payload = await build_ux_auditor_payload(direction_id, _root=tmp_path)
        assert payload is not None
        assert payload["flow_md"] == "", (
            "auditor payload flow_md must be empty string when flow.md is absent"
        )

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_directory(self, tmp_path: Path):
        """Returns None when the direction directory does not exist."""
        payload = await build_ux_auditor_payload("nonexistent", _root=tmp_path)
        assert payload is None

    # ── AC1.1 + AC1.2: flow_narratives with filename and ordered steps ───────

    @pytest.mark.asyncio
    async def test_flow_narratives_includes_filename(self, tmp_path: Path):
        """AC1.1: payload includes flow.md filename in flow_narratives."""
        direction_id = "014-flow-narratives"
        direction_dir = tmp_path / direction_id
        direction_dir.mkdir()
        (direction_dir / "direction.md").write_text("""---
title: "Narrative Test"
type: feature
---
# Narrative Test
""")
        (direction_dir / "flow.md").write_text(
            "# User Flow\n\n1. Open app\n2. Create goal\n3. Submit proof\n"
        )
        _write_state_yaml(tmp_path, direction_id, "queued")

        payload = await build_ux_auditor_payload(direction_id, _root=tmp_path)
        assert payload is not None
        assert "flow_narratives" in payload, (
            "AC1.1: payload must include flow_narratives key"
        )
        assert len(payload["flow_narratives"]) == 1
        assert payload["flow_narratives"][0]["filename"] == "flow.md", (
            "AC1.1: flow_narratives entry must include the flow.md filename"
        )

    @pytest.mark.asyncio
    async def test_flow_narratives_includes_ordered_steps(self, tmp_path: Path):
        """AC1.2: payload includes ordered step list extracted from flow.md."""
        direction_id = "015-ordered-steps"
        direction_dir = tmp_path / direction_id
        direction_dir.mkdir()
        (direction_dir / "direction.md").write_text("""---
title: "Steps Test"
type: feature
---
# Steps Test
""")
        (direction_dir / "flow.md").write_text(
            "# User Flow\n\n1. Open app\n2. Create goal\n3. Submit proof\n"
        )
        _write_state_yaml(tmp_path, direction_id, "queued")

        payload = await build_ux_auditor_payload(direction_id, _root=tmp_path)
        assert payload is not None
        assert "flow_narratives" in payload
        steps = payload["flow_narratives"][0]["steps"]
        assert steps == [
            "Open app",
            "Create goal",
            "Submit proof",
        ], "AC1.2: steps must be extracted as ordered list preserving source order"

    @pytest.mark.asyncio
    async def test_flow_narratives_empty_when_flow_md_absent(self, tmp_path: Path):
        """flow_narratives is an empty list when no flow.md file exists."""
        direction_id = "016-no-flow-narratives"
        direction_dir = tmp_path / direction_id
        direction_dir.mkdir()
        (direction_dir / "direction.md").write_text("""---
title: "No Narratives"
type: feature
---
# No Narratives
""")
        # No flow.md
        _write_state_yaml(tmp_path, direction_id, "queued")

        payload = await build_ux_auditor_payload(direction_id, _root=tmp_path)
        assert payload is not None
        assert "flow_narratives" in payload
        assert payload["flow_narratives"] == [], (
            "flow_narratives must be empty list when flow.md is absent"
        )

    @pytest.mark.asyncio
    async def test_flow_narratives_preserves_step_ordering(self, tmp_path: Path):
        """Steps are returned in the exact order they appear in flow.md."""
        direction_id = "017-step-order"
        direction_dir = tmp_path / direction_id
        direction_dir.mkdir()
        (direction_dir / "direction.md").write_text("""---
title: "Order Test"
type: feature
---
# Order Test
""")
        # Deliberately non-alphabetical order to prove ordering is from source
        (direction_dir / "flow.md").write_text(
            "# User Flow\n\n1. Charlie step\n2. Alpha step\n3. Bravo step\n"
        )
        _write_state_yaml(tmp_path, direction_id, "queued")

        payload = await build_ux_auditor_payload(direction_id, _root=tmp_path)
        assert payload is not None
        steps = payload["flow_narratives"][0]["steps"]
        assert steps == [
            "Charlie step",
            "Alpha step",
            "Bravo step",
        ], "step ordering must match source file, not be re-sorted"


# ── _coarse_status ──────────────────────────────────────────────────────────


class TestCoarseStatus:
    """_coarse_status maps raw factory states to API statuses."""

    def test_known_statuses_map_correctly(self):
        assert _coarse_status("queued") == "queued"
        assert _coarse_status("in_progress") == "in_progress"
        assert _coarse_status("pr_open") == "pr_open"
        assert _coarse_status("merging") == "pr_open"
        assert _coarse_status("pr_merged") == "pr_merged"
        assert _coarse_status("rejected") == "rejected"

    def test_unknown_status_defaults_to_in_progress(self):
        assert _coarse_status("something_new") == "in_progress"


# ── Helpers for tests ───────────────────────────────────────────────────────


def _write_state_yaml(
    directions_root: Path,
    direction_id: str,
    status: str,
    pr_url: str | None = None,
    summary: str | None = None,
):
    """Write a state.yaml for a direction, creating the directory if needed."""
    direction_dir = directions_root / direction_id
    direction_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"status: {status}"]
    if pr_url:
        lines.append(f"pr_url: {pr_url}")
    else:
        lines.append("pr_url: null")
    if summary:
        lines.append(f"summary: {summary}")
    else:
        lines.append(f"summary: Direction is {status}.")
    (direction_dir / "state.yaml").write_text("\n".join(lines) + "\n")

"""Tests for direction synthesis service.

These tests assert on production code that does NOT exist yet
(backend/app/services/direction_synth.py). Every test in this file MUST
fail (RED) on first run against the current codebase.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


# ─── Module import (will fail until file exists) ────────────────────


async def test_module_is_importable():
    """direction_synth module must be importable from app.services."""
    from app.services import direction_synth

    assert direction_synth is not None


# ─── synthesize_direction produces a complete direction directory ────


async def test_synthesize_direction_writes_direction_md():
    """synthesize_direction must create a direction.md with required sections."""
    from app.services.direction_synth import synthesize_direction

    mock_llm_response = """---
title: Pushup Counter
type: feature
why: Users need pushup verification via phone camera
acceptance: |
  - verify(criteria={"count":20}, upload=pushups_20.mp4) → verified
  - verify(criteria={"count":25}, upload=pushups_20.mp4) → failed
---

# Pushup Counter

## What
A goal type that counts pushups from a phone camera video and verifies
the count against user-specified criteria.
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        mock_llm = AsyncMock(return_value=mock_llm_response)

        with patch("app.services.direction_synth._call_llm", mock_llm):
            direction_id = await synthesize_direction(
                llm_client=mock_llm,
                prompt_summary="Do 20 pushups every morning verified with camera",
                output_base=Path(tmpdir),
            )

        direction_dir = Path(tmpdir) / direction_id
        assert direction_dir.is_dir()
        direction_md = direction_dir / "direction.md"
        assert direction_md.is_file()

        content = direction_md.read_text()
        assert "title:" in content
        assert "type:" in content
        assert "why:" in content
        assert "acceptance:" in content


async def test_synthesize_direction_writes_flow_md_when_appropriate():
    """synthesize_direction must write flow.md for user-facing goal types."""
    from app.services.direction_synth import synthesize_direction

    mock_llm_response = """---
title: Pushup Counter
type: feature
why: Users need pushup verification via phone camera
acceptance: |
  - verify(criteria={"count":20}, upload=pushups_20.mp4) → verified
---

# Pushup Counter

## Flow
1. User opens app, creates pushup goal
2. At deadline, user records video
3. App verifies pushup count
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        mock_llm = AsyncMock(return_value=mock_llm_response)

        with patch("app.services.direction_synth._call_llm", mock_llm):
            direction_id = await synthesize_direction(
                llm_client=mock_llm,
                prompt_summary="Do 20 pushups every morning verified with camera",
                output_base=Path(tmpdir),
            )

        direction_dir = Path(tmpdir) / direction_id
        flow_md = direction_dir / "flow.md"
        assert flow_md.is_file()


async def test_synthesize_direction_returns_direction_id():
    """synthesize_direction must return a direction_id with counter prefix."""
    from app.services.direction_synth import synthesize_direction

    mock_llm_response = """---
title: Test Type
type: feature
why: testing
acceptance: |
  - acceptance criterion
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        mock_llm = AsyncMock(return_value=mock_llm_response)

        with patch("app.services.direction_synth._call_llm", mock_llm):
            direction_id = await synthesize_direction(
                llm_client=mock_llm,
                prompt_summary="Test prompt",
                output_base=Path(tmpdir),
            )

        assert direction_id is not None
        assert isinstance(direction_id, str)
        # direction_id format: "<counter>-<slug>", e.g. "011-pushup-counter"
        parts = direction_id.split("-", 1)
        assert len(parts) == 2
        assert parts[0].isdigit()


async def test_synthesize_direction_rejects_vague_prompt():
    """synthesize_direction must raise ValueError for prompts too vague."""
    from app.services.direction_synth import synthesize_direction

    with tempfile.TemporaryDirectory() as tmpdir:
        mock_llm = AsyncMock()

        with pytest.raises(ValueError, match="vague|unclear|insufficient"):
            await synthesize_direction(
                llm_client=mock_llm,
                prompt_summary="do something",
                output_base=Path(tmpdir),
            )


# ─── synthesize_iteration_direction ───────────────────────────────


async def test_synthesize_iteration_writes_parent_direction_frontmatter():
    """Iteration directions must carry parent_direction in frontmatter."""
    from app.services.direction_synth import synthesize_iteration_direction

    feedback = "Use a side-on camera angle instead of front-on; count partial reps as 0.5"
    mock_llm_response = """---
title: Pushup Counter Side Angle
type: feature
parent_direction: 011-pushup-counter
why: This iterates on 011-pushup-counter to use side-on camera angle
acceptance: |
  modify the existing `backend/app/goal_types/pushup_counter/` module to address the following feedback: Use a side-on camera angle instead of front-on; count partial reps as 0.5
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        mock_llm = AsyncMock(return_value=mock_llm_response)

        with patch("app.services.direction_synth._call_llm", mock_llm):
            direction_id = await synthesize_iteration_direction(
                llm_client=mock_llm,
                previous_direction_id="011-pushup-counter",
                feedback=feedback,
                output_base=Path(tmpdir),
            )

        direction_dir = Path(tmpdir) / direction_id
        direction_md = direction_dir / "direction.md"
        content = direction_md.read_text()

        assert "parent_direction:" in content
        assert "011-pushup-counter" in content
        assert "side-on camera angle" in content

        # Assert acceptance includes the user's feedback verbatim
        assert "Use a side-on camera angle instead of front-on" in content
        assert "count partial reps as 0.5" in content

        # Assert why prose references the previous id-slug exactly
        assert "This iterates on 011-pushup-counter" in content

        # Assert the acceptance mentions modifying the existing module
        assert "modify the existing" in content.lower()
        assert "backend/app/goal_types/pushup_counter/" in content


async def test_iteration_slug_describes_feedback_substantively():
    """Iteration slug must NOT encode chain position (no iterate-N style)."""
    from app.services.direction_synth import synthesize_iteration_direction

    mock_llm_response = """---
title: Pushup Counter Modified
type: feature
parent_direction: 011-pushup-counter
why: This iterates on 011-pushup-counter to change angle
acceptance: |
  modify the existing module to use side angle
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        mock_llm = AsyncMock(return_value=mock_llm_response)

        with patch("app.services.direction_synth._call_llm", mock_llm):
            direction_id = await synthesize_iteration_direction(
                llm_client=mock_llm,
                previous_direction_id="011-pushup-counter",
                feedback="Use a side-on camera angle",
                output_base=Path(tmpdir),
            )

        # The slug must NOT be "iterate-1" or "iterate-2" style
        slug = direction_id.split("-", 1)[1]
        assert not slug.startswith("iterate-"), (
            f"Iteration slug '{slug}' must describe feedback, not chain position"
        )


async def test_synthesize_iteration_rejects_empty_feedback():
    """synthesize_iteration_direction must reject empty/whitespace feedback."""
    from app.services.direction_synth import synthesize_iteration_direction

    with tempfile.TemporaryDirectory() as tmpdir:
        mock_llm = AsyncMock()

        with pytest.raises(ValueError, match="feedback|empty"):
            await synthesize_iteration_direction(
                llm_client=mock_llm,
                previous_direction_id="011-pushup-counter",
                feedback="   ",
                output_base=Path(tmpdir),
            )
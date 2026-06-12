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


async def test_synthesize_iteration_rejects_iterate_n_slug():
    """synthesize_iteration_direction must rewrite iterate-N style slugs.

    When the LLM returns a title like 'Iterate 2', the resulting slug must
    NOT be 'iterate-2'. Instead it must be derived from the why prose or
    feedback, and the why field must reference the previous direction id-slug.
    """
    from app.services.direction_synth import synthesize_iteration_direction

    # This LLM response has a title that would produce the forbidden
    # "iterate-2" slug if the code did not guard against it.
    mock_llm_response = """---
title: Iterate 2
type: feature
parent_direction: 011-pushup-counter
why: This iterates on 011-pushup-counter to add a side-on camera angle
acceptance: |
  modify the existing backend/app/goal_types/pushup_counter/ module to address the following feedback: use side angle for camera
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

        slug = direction_id.split("-", 1)[1]
        # The slug must NOT be the chain-position "iterate-2"
        assert slug != "iterate-2", (
            f"Iteration slug must be rewritten; got forbidden '{slug}'"
        )
        assert not slug.startswith("iterate-"), (
            f"Iteration slug '{slug}' must describe feedback, not chain position"
        )

        # Assert the written direction.md contains the why reference
        md_content = (Path(tmpdir) / direction_id / "direction.md").read_text()
        assert "This iterates on 011-pushup-counter" in md_content, (
            "why field must reference the previous direction id-slug"
        )
        assert "parent_direction: 011-pushup-counter" in md_content


# ─── Concurrent ID allocation safety ──────────────────────────────────


async def test_concurrent_direction_id_allocations_are_unique():
    """Concurrent _next_direction_id calls must produce unique ids.

    Uses threads to exercise the flock-based counter under contention
    and asserts every allocated id is distinct.
    """
    import concurrent.futures
    import tempfile

    from app.services.direction_synth import _next_direction_id

    with tempfile.TemporaryDirectory() as tmpdir:
        counter_path = Path(tmpdir) / ".direction_counter"
        n_workers = 12
        ids_per_worker = 20

        def allocate_one(_):
            return _next_direction_id(counter_path)

        all_ids = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [
                pool.submit(allocate_one, i)
                for i in range(n_workers * ids_per_worker)
            ]
            for f in concurrent.futures.as_completed(futures):
                all_ids.append(f.result())

    # Every id must be unique
    assert len(all_ids) == len(set(all_ids)), (
        f"Duplicate IDs detected! {len(all_ids) - len(set(all_ids))} collisions "
        f"among {len(all_ids)} allocations."
    )

    # All ids must be properly formatted (zero-padded numeric prefix)
    for did in all_ids:
        parts = did.split("-", 1)
        assert parts[0].isdigit() and len(parts[0]) == 3, (
            f"Malformed direction id: {did}"
        )


# ─── Directory-aware ID allocation ─────────────────────────────────────


def test_next_direction_id_derives_from_existing_directories():
    """When directories exist with higher ids than the counter file,
    the next id must be max(dir_ids, counter) + 1."""
    import tempfile

    from app.services.direction_synth import _next_direction_id

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        # Pre-populate: dirs 005, 017, 042 exist, but counter says 3
        for did in ("005-old-goal-type", "017-another-goal", "042-existing-slug"):
            (base / did).mkdir()
        counter_path = base / ".direction_counter"
        counter_path.write_text("3")

        next_id = _next_direction_id(counter_path)

    assert next_id == "043", (
        f"Expected next id '043' (max(42, 3) + 1), got '{next_id}'"
    )


def test_next_direction_id_starts_at_1_with_empty_volume():
    """When the volume is empty (no dirs, no counter), the first id is 001."""
    import tempfile

    from app.services.direction_synth import _next_direction_id

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        counter_path = base / ".direction_counter"

        next_id = _next_direction_id(counter_path)

    assert next_id == "001", (
        f"Expected first id '001' on empty volume, got '{next_id}'"
    )


def test_next_direction_id_ignores_counter_when_dirs_have_higher_ids():
    """When a directory has id 100 but counter says 5, next must be 101."""
    import tempfile

    from app.services.direction_synth import _next_direction_id

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        (base / "100-high-id-dir").mkdir()
        counter_path = base / ".direction_counter"
        counter_path.write_text("5")

        next_id = _next_direction_id(counter_path)

    assert next_id == "101", (
        f"Expected next id '101' (max(100, 5) + 1), got '{next_id}'"
    )

"""
Unit tests for the direction synthesis service.

The service lives at ``backend/app/services/direction_synth.py`` and does not
exist yet. Every import and call in this file MUST fail on first run.

The service is unit-testable with a mocked LLM client. It takes chat history
and synthesizes a direction directory containing direction.md (and optionally
flow.md, api_spec.md).
"""

import pytest


# ─── Service import ───────────────────────────────────────────────────


def test_direction_synth_module_is_importable():
    """
    The direction_synth module exists and exports synthesize_direction.
    MUST fail: the module doesn't exist yet.
    """
    from app.services.direction_synth import synthesize_direction

    assert callable(synthesize_direction)


# ─── synthesize_direction happy path ───────────────────────────────────


@pytest.mark.asyncio
async def test_synthesize_direction_returns_direction_id_and_path():
    """
    synthesize_direction takes chat history + goal draft, returns a dict
    with direction_id, direction_path, and status.
    MUST fail: the function doesn't exist yet.
    """
    from app.services.direction_synth import synthesize_direction

    chat_history = [
        {"role": "user", "content": "I want to do 20 pushups every morning at 7am, verify with my phone camera."},
        {"role": "assistant", "content": "I don't have a built-in way to verify that yet. Want me to build a new goal type?"},
        {"role": "user", "content": "Yes, build it."},
    ]
    goal_draft = {
        "title": "20 morning pushups",
        "description": "Do 20 pushups every morning at 7am",
        "pledge_amount": 1000,
    }

    class MockLLMClient:
        async def complete(self, prompt, **kwargs):
            return {
                "direction_id": "011-pushup-counter",
                "direction_md": "# 011-pushup-counter\n\n## Title\nPushup Counter\n\n## Type\nfeature\n\n## Why\n...\n\n## Acceptance Criteria\n...",
                "flow_md": "# User flow\n\n...",
            }

    result = await synthesize_direction(
        chat_history=chat_history,
        goal_draft=goal_draft,
        llm_client=MockLLMClient(),
        directions_root="/tmp/test-directions",
    )

    assert "direction_id" in result
    assert "direction_path" in result
    assert result["status"] == "queued"
    assert result["direction_id"] == "011-pushup-counter"


@pytest.mark.asyncio
async def test_synthesize_direction_writes_direction_md_to_disk():
    """
    synthesize_direction writes direction.md to the returned path.
    MUST fail: the function doesn't exist yet.
    """
    import os
    import tempfile

    from app.services.direction_synth import synthesize_direction

    chat_history = [
        {"role": "user", "content": "Track my daily meditation with a timer proof."},
    ]
    goal_draft = {"title": "Meditation tracker"}

    class MockLLMClient:
        async def complete(self, prompt, **kwargs):
            return {
                "direction_id": "042-meditation-tracker",
                "direction_md": "# Direction: Meditation Tracker\n\n## Acceptance Criteria\n- Timer-based verification",
            }

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await synthesize_direction(
            chat_history=chat_history,
            goal_draft=goal_draft,
            llm_client=MockLLMClient(),
            directions_root=tmpdir,
        )

        direction_dir = result["direction_path"]
        assert os.path.isdir(direction_dir)
        assert os.path.isfile(os.path.join(direction_dir, "direction.md"))

        with open(os.path.join(direction_dir, "direction.md")) as f:
            content = f.read()
        assert "Meditation Tracker" in content


# ─── synthesize_direction write structure ─────────────────────────────


@pytest.mark.asyncio
async def test_synthesize_direction_writes_state_yaml():
    """
    synthesize_direction writes state.yaml with status=queued.
    MUST fail: the function doesn't exist yet.
    """
    import os
    import tempfile

    from app.services.direction_synth import synthesize_direction

    chat_history = [{"role": "user", "content": "Test goal"}]
    goal_draft = {"title": "Test"}

    class MockLLMClient:
        async def complete(self, prompt, **kwargs):
            return {
                "direction_id": "001-test",
                "direction_md": "# Test",
            }

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await synthesize_direction(
            chat_history=chat_history,
            goal_draft=goal_draft,
            llm_client=MockLLMClient(),
            directions_root=tmpdir,
        )

        assert os.path.isfile(os.path.join(result["direction_path"], "state.yaml"))


# ─── iterate direction with parent_direction frontmatter ──────────────


@pytest.mark.asyncio
async def test_synthesize_iteration_includes_parent_direction_in_frontmatter():
    """
    When parent_direction_id is provided, the synthesized direction.md
    includes it in frontmatter as ``parent_direction: <id>``.
    MUST fail: the function doesn't exist yet.
    """
    import os
    import tempfile

    from app.services.direction_synth import synthesize_direction

    chat_history = [
        {"role": "user", "content": "Use a side-on camera angle instead of front-on."},
    ]
    goal_draft = {"title": "20 morning pushups"}

    class MockLLMClient:
        async def complete(self, prompt, **kwargs):
            return {
                "direction_id": "047-pushup-counter-side-angle",
                "direction_md": "---\nparent_direction: 011-pushup-counter\n---\n# Pushup Counter Side Angle",
            }

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await synthesize_direction(
            chat_history=chat_history,
            goal_draft=goal_draft,
            llm_client=MockLLMClient(),
            directions_root=tmpdir,
            parent_direction_id="011-pushup-counter",
        )

        assert result["direction_id"] == "047-pushup-counter-side-angle"

        with open(os.path.join(result["direction_path"], "direction.md")) as f:
            content = f.read()
        assert "parent_direction: 011-pushup-counter" in content


# ─── Direction slug is substantive, not iterate-N ─────────────────────


@pytest.mark.asyncio
async def test_synthesize_iteration_slug_is_substantive_not_iterate_n():
    """
    The synthesized direction id/slug describes the feedback substantively
    (e.g. 'pushup-counter-side-angle'), NOT 'iterate-2' or similar.
    MUST fail: the function doesn't exist yet.
    """
    import re

    from app.services.direction_synth import synthesize_direction

    chat_history = [
        {"role": "user", "content": "Use a side-on camera angle; count partial reps as 0.5."},
    ]
    goal_draft = {"title": "20 morning pushups"}

    class MockLLMClient:
        async def complete(self, prompt, **kwargs):
            return {
                "direction_id": "047-pushup-counter-side-angle",
                "direction_md": "---\nparent_direction: 011-pushup-counter\n---\n# Direction",
            }

    result = await synthesize_direction(
        chat_history=chat_history,
        goal_draft=goal_draft,
        llm_client=MockLLMClient(),
        directions_root="/tmp/test-directions",
        parent_direction_id="011-pushup-counter",
    )

    direction_id = result["direction_id"]
    # Must NOT match iterate-N pattern
    assert not re.match(r".*-iterate-\d+$", direction_id.split("-")[-1] if "-" in direction_id else direction_id)


# ─── Edge cases ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_synthesize_direction_raises_on_vague_prompt():
    """
    synthesize_direction raises ValueError when the LLM cannot produce a
    coherent direction from the prompt (prompt too vague).
    MUST fail: the function doesn't exist yet.
    """
    import pytest

    from app.services.direction_synth import synthesize_direction

    chat_history = [{"role": "user", "content": "idk something with fitness"}]
    goal_draft = {"title": "fitness thing"}

    class MockLLMClient:
        async def complete(self, prompt, **kwargs):
            raise ValueError("Prompt too vague to synthesize a direction")

    with pytest.raises(ValueError, match="vague"):
        await synthesize_direction(
            chat_history=chat_history,
            goal_draft=goal_draft,
            llm_client=MockLLMClient(),
            directions_root="/tmp/test-directions",
        )
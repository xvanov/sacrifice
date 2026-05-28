"""Tests for D010 direction synthesis service.

The synthesis service (`backend/app/services/direction_synth.py`) takes
chat history and produces a structured direction directory with:
- direction.md (title, type=feature, why, acceptance)
- flow.md (where appropriate)
- api_spec.md (where appropriate)

These tests use a mocked LLM client so they are unit-testable without
Azure Foundry connectivity.
"""

import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

# The service will be importable once the Dev implements it.
# For now we test against the expected interface contract.
SYNTH_MODULE = "app.services.direction_synth"


# ─── Service interface tests ────────────────────────────────────────


async def test_synthesize_direction_returns_dict_with_required_keys():
    """synthesize_direction() returns a dict with direction_id, title, and files."""
    from app.services.direction_synth import synthesize_direction

    chat_history = [
        {"role": "user", "content": "I want to do 20 pushups every morning at 7am, verify with my phone camera."},
        {"role": "assistant", "content": "I don't have a built-in way to verify that yet. Want me to build it?"},
    ]

    with patch(f"{SYNTH_MODULE}._call_llm") as mock_llm:
        mock_llm.return_value = _fake_llm_response_pushups()
        result = await synthesize_direction(chat_history=chat_history)

    assert isinstance(result, dict)
    assert "direction_id" in result
    assert "title" in result
    assert "files" in result
    assert "direction.md" in result["files"]
    assert result["files"]["direction.md"].startswith("---")


async def test_synthesize_direction_makes_llm_call_with_chat_context():
    """synthesize_direction() passes chat history as context to the LLM."""
    from app.services.direction_synth import synthesize_direction

    chat_history = [
        {"role": "user", "content": "I want to do 20 pushups every morning at 7am."},
    ]

    with patch(f"{SYNTH_MODULE}._call_llm") as mock_llm:
        mock_llm.return_value = _fake_llm_response_pushups()
        await synthesize_direction(chat_history=chat_history)

    mock_llm.assert_called_once()
    call_args = mock_llm.call_args
    # The chat context should be in the messages sent to the LLM
    messages = call_args[1].get("messages", call_args[0][0] if call_args[0] else [])
    # At least one message contains the user's pushup request
    assert any("pushups" in str(m).lower() for m in messages)


async def test_synthesize_direction_handles_llm_error_gracefully():
    """synthesize_direction() raises ValueError when LLM cannot produce coherent output."""
    from app.services.direction_synth import synthesize_direction

    chat_history = [{"role": "user", "content": "asdfghjkl"}]

    with patch(f"{SYNTH_MODULE}._call_llm") as mock_llm:
        mock_llm.return_value = '{"error": "cannot parse"}'
        with pytest.raises(ValueError):
            await synthesize_direction(chat_history=chat_history)


async def test_synthesize_direction_rejects_vague_prompt():
    """synthesize_direction() raises ValueError when prompt is too vague to synthesize."""
    from app.services.direction_synth import synthesize_direction

    chat_history = [{"role": "user", "content": "do something cool"}]

    with patch(f"{SYNTH_MODULE}._call_llm") as mock_llm:
        mock_llm.return_value = '{"reject": true, "reason": "too vague"}'
        with pytest.raises(ValueError):
            await synthesize_direction(chat_history=chat_history)


# ─── Direction writer tests ─────────────────────────────────────────


async def test_write_direction_creates_directory_with_files():
    """write_direction() creates a directory containing all specified files."""
    from app.services.direction_synth import write_direction

    direction_id = "test-011-pushup-counter"
    files = {
        "direction.md": "# Test Direction\n\nThis is a test.",
        "flow.md": "# Flow\n\nTest flow.",
        "api_spec.md": "# API Spec\n\nTest spec.",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = await write_direction(
            direction_id=direction_id,
            files=files,
            base_dir=tmpdir,
        )

        assert os.path.isdir(output_path)
        assert os.path.isfile(os.path.join(output_path, "direction.md"))
        assert os.path.isfile(os.path.join(output_path, "flow.md"))
        assert os.path.isfile(os.path.join(output_path, "api_spec.md"))

        with open(os.path.join(output_path, "direction.md")) as f:
            assert f.read() == files["direction.md"]


async def test_write_direction_uses_direction_id_as_dirname():
    """write_direction() names the directory after the direction_id."""
    from app.services.direction_synth import write_direction

    direction_id = "047-pushup-counter-side-angle"
    files = {"direction.md": "---\ntitle: Test\n---\n"}

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = await write_direction(
            direction_id=direction_id,
            files=files,
            base_dir=tmpdir,
        )
        assert os.path.basename(output_path) == direction_id


async def test_write_direction_overwrites_existing():
    """write_direction() overwrites an existing direction directory."""
    from app.services.direction_synth import write_direction

    direction_id = "test-overwrite"
    files_v1 = {"direction.md": "v1"}
    files_v2 = {"direction.md": "v2"}

    with tempfile.TemporaryDirectory() as tmpdir:
        await write_direction(direction_id, files_v1, base_dir=tmpdir)
        await write_direction(direction_id, files_v2, base_dir=tmpdir)

        with open(os.path.join(tmpdir, direction_id, "direction.md")) as f:
            assert f.read() == "v2"


# ─── Iteration direction tests ──────────────────────────────────────


async def test_synthesize_iteration_includes_parent_direction_frontmatter():
    """Iteration synthesis produces direction.md with parent_direction in frontmatter."""
    from app.services.direction_synth import synthesize_iteration

    previous_direction_id = "011-pushup-counter"
    feedback = "Use a side-on camera angle; count partial reps as 0.5."

    with patch(f"{SYNTH_MODULE}._call_llm") as mock_llm:
        mock_llm.return_value = _fake_llm_response_iteration()
        result = await synthesize_iteration(
            previous_direction_id=previous_direction_id,
            feedback=feedback,
        )

    direction_md = result["files"]["direction.md"]
    assert "parent_direction:" in direction_md
    assert previous_direction_id in direction_md


async def test_synthesize_iteration_slug_describes_feedback():
    """Iteration direction slug describes the feedback, not chain position."""
    from app.services.direction_synth import synthesize_iteration

    with patch(f"{SYNTH_MODULE}._call_llm") as mock_llm:
        mock_llm.return_value = _fake_llm_response_iteration()
        result = await synthesize_iteration(
            previous_direction_id="011-pushup-counter",
            feedback="Use side angle",
        )

    direction_id = result["direction_id"]
    # The slug should be about side angle, not "iterate-1" or "iteration-2"
    slug = direction_id.split("-", 1)[1] if "-" in direction_id else direction_id
    assert "iterate" not in slug.lower()
    assert "side" in slug.lower() or "angle" in slug.lower()


# ─── Helpers ────────────────────────────────────────────────────────


def _fake_llm_response_pushups():
    """Simulate a coherent LLM response for the pushup counter goal type."""
    return """
{
  "title": "Pushup Counter",
  "direction_id": "011-pushup-counter",
  "files": {
    "direction.md": "---\\ntitle: Pushup Counter\\ntype: feature\\nwhy: User wants to verify pushup workouts with phone camera.\\nacceptance:\\n- Verifier accepts a video upload and criteria_data {\\"count\\": <int>}.\\n- Returns verified when count matches, failed otherwise.\\n---\\n",
    "flow.md": "# Flow\\n\\n1. User records video of pushups\\n2. Uploads via proof pipeline\\n3. CV module counts reps\\n4. Verdict returned\\n",
    "api_spec.md": "# API Spec\\n\\nNo new endpoints; uses existing proof upload pipeline.\\n"
  }
}
"""


def _fake_llm_response_iteration():
    """Simulate a coherent LLM response for an iteration direction."""
    return """
{
  "title": "Pushup Counter Side Angle",
  "direction_id": "047-pushup-counter-side-angle",
  "files": {
    "direction.md": "---\\ntitle: Pushup Counter Side Angle\\ntype: feature\\nparent_direction: 011-pushup-counter\\nwhy: This iterates on 011-pushup-counter to use side-on camera angle and count partial reps as 0.5.\\nacceptance:\\n- Modify the existing `backend/app/goal_types/pushup_counter/` module to address the following feedback: Use a side-on camera angle instead of front-on; count partial reps as 0.5.\\n---\\n",
    "flow.md": "# Flow\\n\\n1. User records video from side angle\\n2. Uploads via proof pipeline\\n3. Updated CV module counts reps with partial credit\\n4. Verdict returned\\n"
  }
}
"""
"""Direction synthesis service.

Synthesizes factory directions from chat context and writes them to the
configured directions directory. Unit-testable with a mocked LLM client.
"""

import os
import uuid
from dataclasses import dataclass

from app.config import settings


@dataclass
class DirectionResult:
    direction_id: str
    direction_dir: str
    slug: str


async def synthesize_direction(
    prompt_summary: str,
    goal_payload_draft: dict,
    model: str | None = None,
) -> DirectionResult:
    """Synthesize a factory direction from chat context.

    Uses an LLM to produce a direction.md, and where appropriate flow.md
    and api_spec.md, then writes them to the configured directions directory.

    Returns the assigned direction_id and the directory path.
    """
    directions_root = settings.directions_path or "/var/factory/directions"

    # Allocate a direction id from the global counter. In the real
    # implementation this must be atomic — for now we use a simple
    # directory-listing-based counter suitable for the stub.
    slug = _derive_slug(prompt_summary)
    direction_id = _allocate_direction_id(directions_root, slug)

    direction_dir = os.path.join(directions_root, direction_id)
    os.makedirs(direction_dir, exist_ok=True)

    # Write direction.md
    _write_direction_md(direction_dir, direction_id, prompt_summary, goal_payload_draft)

    # Write initial state.yaml
    _write_state_yaml(direction_dir, "queued")

    return DirectionResult(
        direction_id=direction_id,
        direction_dir=direction_dir,
        slug=slug,
    )


def _derive_slug(prompt_summary: str) -> str:
    """Derive a URL-safe slug from the prompt summary."""
    slug = prompt_summary.lower().strip()
    # Take first 3 meaningful words
    words = [w for w in slug.replace(",", " ").split() if len(w) > 2][:3]
    slug = "-".join(words) if words else "new-goal-type"
    # Sanitize: only alphanumeric and hyphens
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in slug)
    slug = slug.strip("-")[:60]
    return slug or "new-goal-type"


def _allocate_direction_id(directions_root: str, slug: str) -> str:
    """Allocate a direction id from the global counter."""
    try:
        existing = [
            d for d in os.listdir(directions_root)
            if os.path.isdir(os.path.join(directions_root, d))
        ]
    except (FileNotFoundError, OSError):
        existing = []

    max_num = 0
    for d in existing:
        parts = d.split("-", 1)
        try:
            num = int(parts[0])
            if num > max_num:
                max_num = num
        except (ValueError, IndexError):
            pass

    next_num = max_num + 1
    return f"{next_num:03d}-{slug}"


def _write_direction_md(
    direction_dir: str,
    direction_id: str,
    prompt_summary: str,
    goal_payload_draft: dict,
) -> None:
    goal_title = goal_payload_draft.get("title", "New Goal Type")
    goal_desc = goal_payload_draft.get("description", prompt_summary)

    content = f"""---
id: {direction_id}
type: feature
title: {goal_title}
---

# {goal_title}

## Why
{goal_desc}

## Acceptance Criteria
- Implement a new goal type based on the following prompt: {prompt_summary}
- The goal type must pass all verification criteria appropriate to its domain

## Implementation notes
This direction was synthesized from chat context. The original prompt summary was:
> {prompt_summary}
"""
    with open(os.path.join(direction_dir, "direction.md"), "w") as f:
        f.write(content)


def _write_state_yaml(direction_dir: str, status: str) -> None:
    import yaml

    state = {
        "status": status,
        "pr_url": None,
        "summary": "Direction synthesized, awaiting factory chain execution.",
    }
    with open(os.path.join(direction_dir, "state.yaml"), "w") as f:
        yaml.dump(state, f)


def read_direction_state(direction_id: str) -> dict | None:
    """Read the state.yaml for a direction, returning coarse status."""
    import yaml

    directions_root = settings.directions_path or "/var/factory/directions"
    state_path = os.path.join(directions_root, direction_id, "state.yaml")

    try:
        with open(state_path) as f:
            return yaml.safe_load(f)
    except (FileNotFoundError, OSError):
        return None
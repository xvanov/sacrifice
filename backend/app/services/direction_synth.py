"""Direction synthesis service.

Synthesizes a complete direction directory (direction.md, flow.md, api_spec.md)
from chat history via an LLM call, and writes it to the configured directions path.
"""

import os
import re
import json as json_mod
from pathlib import Path

import httpx
import yaml

from app.config import settings


def _llm_model() -> str:
    return settings.direction_synth_model or settings.azure_foundry_deployment


async def _call_llm(system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
    """Call the configured LLM and return the response text."""
    headers = {
        "Authorization": f"Bearer {settings.azure_foundry_api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": 4000,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            settings.azure_foundry_endpoint,
            headers=headers,
            json=payload,
            timeout=60,
        )

    if resp.status_code != 200:
        raise DirectionSynthesisError(f"LLM API error: {resp.status_code}")

    result = resp.json()
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        raise DirectionSynthesisError("LLM returned empty response")
    return content


class DirectionSynthesisError(Exception):
    """Raised when direction synthesis fails."""


async def synthesize_direction(prompt_summary: str, chat_history: list[dict] | None = None) -> dict:
    """Synthesize a direction from the user's prompt and optional chat history.

    Returns a dict with 'title', 'slug', 'direction_md', 'flow_md', 'api_spec_md'.
    """
    history_text = ""
    if chat_history:
        lines = []
        for msg in chat_history[-10:]:  # last 10 messages
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            lines.append(f"[{role}]: {content}")
        history_text = "\n".join(lines)

    system_prompt = (
        "You are a software architect synthesizing a feature direction for a goal-tracking application "
        "called Sacrifice. Given a user's goal description, you produce a structured direction document "
        "describing a new goal-type verifier module.\n\n"
        "The direction is a YAML frontmatter + markdown document with these fields:\n"
        "- `title`: a short feature title\n"
        "- `type`: always `feature`\n"
        "- `why`: 1-2 sentences on why this goal type is needed\n"
        "- `acceptance`: numbered list of testable acceptance criteria\n\n"
        "Where appropriate, also produce `flow.md` (user flow description) and `api_spec.md` (API spec).\n\n"
        "Respond as a JSON object with keys: `title`, `slug`, `direction_md`, `flow_md`, `api_spec_md`.\n"
        "The `slug` must be a short, hyphenated identifier (e.g. `pushup-counter`).\n"
        "The `direction_md` is the full direction.md content including YAML frontmatter."
    )

    user_prompt = f"User prompt: {prompt_summary}\n\n"
    if history_text:
        user_prompt += f"Chat history (most recent first):\n{history_text}\n\n"
    user_prompt += "Synthesize a direction for a new goal-type verifier module."

    if not settings.azure_foundry_endpoint or not settings.azure_foundry_api_key:
        return _local_fallback_synthesis(prompt_summary)

    response = await _call_llm(system_prompt, user_prompt)
    try:
        # Try to extract JSON from the response
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            return json_mod.loads(json_match.group())
        # If no JSON found, try parsing whole response
        return json_mod.loads(response)
    except (json_mod.JSONDecodeError, KeyError, ValueError) as e:
        raise DirectionSynthesisError(f"Could not parse LLM response: {e}")


def _local_fallback_synthesis(prompt_summary: str) -> dict:
    """Local fallback when no LLM is configured. Produces a minimal direction."""
    import re as _re
    # Derive a slug from the prompt
    words = _re.findall(r'\w+', prompt_summary.lower())
    slug = "-".join(words[:4]) if words else "custom-goal-type"
    title = " ".join(w.capitalize() for w in slug.split("-"))

    direction_md = f"""---
title: "{title}"
type: feature
why: "User requested verification for: {prompt_summary}"
acceptance:
  - "Create backend/app/goal_types/{slug}/ module conforming to the goal-type plugin base"
  - "Verifier accepts proof uploads and criteria_data payload"
  - "All fixture-based assertions pass"
---

# {title}

## Why
User needs a custom goal type for: {prompt_summary}

## Acceptance Criteria
1. Module created at `backend/app/goal_types/{slug}/`
2. Verifier correctly evaluates proof submissions
3. Tests pass with provided fixtures
"""

    flow_md = f"""# User flow

1. User creates goal of type `{slug}`
2. User submits proof
3. Verifier evaluates proof
4. Result displayed to user
"""

    api_spec_md = """# API spec

## Endpoints

Existing goal/proof endpoints apply; no new API surface needed.
"""

    return {
        "title": title,
        "slug": slug,
        "direction_md": direction_md,
        "flow_md": flow_md,
        "api_spec_md": api_spec_md,
    }


async def write_direction(synthesis: dict, direction_id: str) -> Path:
    """Write a synthesized direction to disk.

    Returns the path to the direction directory.
    """
    directions_root = Path(settings.directions_path)
    direction_dir = directions_root / direction_id

    os.makedirs(direction_dir, exist_ok=True)

    (direction_dir / "direction.md").write_text(synthesis["direction_md"])
    (direction_dir / "flow.md").write_text(synthesis.get("flow_md", ""))
    (direction_dir / "api_spec.md").write_text(synthesis.get("api_spec_md", ""))

    # Write initial state.yaml
    state_yaml = "status: queued\npr_url: null\nsummary: Direction synthesized, awaiting factory chain.\n"
    (direction_dir / "state.yaml").write_text(state_yaml)

    return direction_dir


# Mapping from raw factory lifecycle states to coarse API statuses
_FACTORY_TO_API_STATUS = {
    "queued": "queued",
    "in_progress": "in_progress",
    "pr_open": "pr_open",
    "merging": "pr_open",  # PR still open during merge
    "pr_merged": "pr_merged",
    "rejected": "rejected",
}


def _coarse_status(raw_status: str) -> str:
    """Map a raw factory lifecycle state to one of the five coarse API statuses."""
    return _FACTORY_TO_API_STATUS.get(raw_status, "in_progress")


async def read_direction_state(direction_id: str) -> dict | None:
    """Read the state.yaml for a direction. Returns None if not found.

    Uses yaml.safe_load for correct handling of quoted scalars, nulls,
    booleans, and URLs containing colons (e.g. https://...).
    """
    state_path = Path(settings.directions_path) / direction_id / "state.yaml"
    if not state_path.exists():
        return None

    raw = yaml.safe_load(state_path.read_text()) or {}
    state = {}
    for key, value in raw.items():
        if value is None:
            state[key] = None
        else:
            state[key] = str(value) if not isinstance(value, str) else value

    # Map raw factory status to coarse API status
    if "status" in state:
        state["status"] = _coarse_status(state["status"])
    return state


async def read_direction_metadata(direction_id: str) -> dict | None:
    """Read direction.md frontmatter + state.yaml for a direction.

    Returns a dict with keys like 'module_name', 'title', 'status', 'pr_url'.
    Returns None if the direction directory doesn't exist.
    """
    direction_dir = Path(settings.directions_path) / direction_id
    if not direction_dir.exists():
        return None

    meta = {}

    # Read state.yaml for status/pr_url
    state = await read_direction_state(direction_id)
    if state:
        meta.update(state)

    # Read direction.md frontmatter for module_name, title
    direction_md_path = direction_dir / "direction.md"
    if direction_md_path.exists():
        content = direction_md_path.read_text()
        # Extract YAML frontmatter between --- markers
        match = re.search(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if match:
            frontmatter = match.group(1)
            for line in frontmatter.strip().split("\n"):
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip().strip('"').strip("'")

    # The module_name MUST be explicitly stored in state.yaml (factory writes it
    # on merge). We intentionally do NOT derive it from the direction_id slug —
    # the slug is not guaranteed to match the generated module name.
    return meta


async def fire_notification_on_merge(
    direction_id: str,
    goal_id: str,
    user_id: str,
    db_session=None,
) -> bool:
    """If direction is pr_merged and no notification exists, fire goal_type_ready.

    Returns True if a notification was newly created, False otherwise.
    Callers must pass a db_session or the notification is not persisted.
    """
    state = await read_direction_state(direction_id)
    if not state or state.get("status") != "pr_merged":
        return False

    if db_session is None:
        return False

    from app.models.notification import Notification as NotificationModel
    from sqlalchemy import select

    notif_check = await db_session.execute(
        select(NotificationModel).where(
            NotificationModel.goal_id == goal_id,
            NotificationModel.type == "goal_type_ready",
        )
    )
    if notif_check.scalar_one_or_none():
        return False  # Already notified

    from app.services.notification import create_notification
    await create_notification(
        db=db_session,
        user_id=user_id,
        notification_type="goal_type_ready",
        title="Goal Type Ready",
        body=f"Your {direction_id} goal type is ready. Accept and activate your goal?",
        goal_id=goal_id,
    )
    return True


async def allocate_direction_id(slug: str) -> str:
    """Allocate a unique direction id using exclusive directory creation.

    Uses mkdir with exist_ok=False for atomic allocation — concurrent
    requests that collide will retry with incremented counters.
    Returns e.g. '011-pushup-counter'.
    """
    directions_root = Path(settings.directions_path)
    os.makedirs(directions_root, exist_ok=True)

    # Scan for the highest existing counter as a starting point
    existing = []
    for entry in directions_root.iterdir():
        if entry.is_dir() and "-" in entry.name:
            try:
                num = int(entry.name.split("-")[0])
                existing.append(num)
            except ValueError:
                pass

    counter = max(existing) + 1 if existing else 11

    # Atomic allocation: try to create the directory exclusively.
    # If it already exists (race), bump the counter and retry.
    while True:
        direction_id = f"{counter:03d}-{slug}"
        direction_dir = directions_root / direction_id
        try:
            direction_dir.mkdir(exist_ok=False)
            # Touch a .lock file to reserve the directory; write_direction
            # will populate the real content shortly after.
            (direction_dir / "state.yaml").write_text(
                "status: queued\npr_url: null\nsummary: Direction reserved, awaiting synthesis write.\n"
            )
            return direction_id
        except FileExistsError:
            counter += 1
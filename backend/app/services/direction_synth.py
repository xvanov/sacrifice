"""Direction synthesis service.

Synthesizes complete direction directories from chat context using an LLM,
and supports iteration (filing follow-up directions with parent linkage).
"""

from __future__ import annotations

import json as json_mod
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import settings

# ─── minimum viable prompt patterns ────────────────────────────────

_VAGUE_PATTERNS = [
    r"^do something",
    r"^i want to do something",
    r"^something",
    r"^help",
    r"^what",
    r"^how",
    r"^can you",
    r"^do you",
    r"^tell me",
]


def _is_vague(prompt: str) -> bool:
    stripped = prompt.strip().lower()
    if len(stripped.split()) < 2:
        return True
    for pat in _VAGUE_PATTERNS:
        if re.match(pat, stripped):
            return True
    return False


def _slugify(text: str, max_words: int = 6) -> str:
    """Derive a hyphenated slug from text."""
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return "-".join(words[:max_words]) if words else "new-goal-type"


async def _call_llm(llm_client, system_prompt: str, user_prompt: str) -> str:
    """Call the configured LLM and return the content string.

    If llm_client has a .chat method, use that (test mock). Otherwise use
    the configured Azure Foundry endpoint. If no endpoint is configured,
    use a local fallback.
    """
    if hasattr(llm_client, "chat"):
        return await llm_client.chat(system_prompt, user_prompt)

    if settings.azure_foundry_endpoint and settings.azure_foundry_api_key:
        headers = {
            "Authorization": f"Bearer {settings.azure_foundry_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 4000,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                settings.azure_foundry_endpoint,
                headers=headers,
                json=payload,
                timeout=120,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"LLM API error: {resp.status_code} {resp.text[:200]}")
        result = resp.json()
        return result.get("choices", [{}])[0].get("message", {}).get("content", "")

    # Local fallback: produce a minimal direction
    return json_mod.dumps({
        "title": user_prompt[:80],
        "type": "feature",
        "why": user_prompt,
        "acceptance": [user_prompt],
    })


def _extract_json_block(content: str) -> dict:
    """Extract a JSON object from LLM output (may be wrapped in markdown)."""
    # Try to find ```json ... ``` block first
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if m:
        content = m.group(1).strip()
    # Find outermost {}
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start : end + 1]
    return json_mod.loads(content)


async def synthesize_direction(
    llm_client,
    prompt_summary: str,
    output_base: Path,
) -> str:
    """Synthesize a complete direction directory from chat context.

    This function takes an LLM client (to be mockable) and an output
    directory base, writes the direction files, and returns the assigned
    direction_id.

    Raises ValueError if the prompt is too vague.
    Raises RuntimeError if LLM call fails.
    """
    if _is_vague(prompt_summary):
        raise ValueError(
            "prompt:too_vague: Prompt is too vague to synthesize a direction."
        )

    # Build the prompt
    system_prompt = (
        "You are a technical architect writing a software factory direction. "
        "Given a user's goal description, produce a complete direction specification "
        "in YAML-frontmatter + markdown format.\n\n"
        "Your output must be a YAML block (delimited by --- lines) containing these "
        "keys:\n"
        '  - "title": A short title for the direction\n'
        '  - "type": Always "feature"\n'
        '  - "why": Why this direction exists\n'
        '  - "acceptance": A pipe-delimited block of acceptance criteria\n\n'
        "Followed by a markdown body with sections '## What' and optionally "
        "'## Flow' if the feature needs a user flow.\n\n"
        "Return ONLY the frontmatter + markdown, no code fences."
    )

    user_prompt = (
        f"User prompt: {prompt_summary}\n"
    )

    content = await _call_llm(llm_client, system_prompt, user_prompt)
    return _write_synth_result(content, output_base)


def _parse_frontmatter(md_text: str) -> dict:
    """Parse YAML-like frontmatter from markdown.

    Returns a dict with title, type, why, acceptance keys extracted.
    """
    lines = md_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}

    fm_lines = lines[1:end_idx]
    data = {}
    current_key = None
    current_val: list[str] = []

    for line in fm_lines:
        m = re.match(r"^(\w[\w\s]*?):(?:\s+(.*))?$", line)
        if m:
            if current_key:
                value = current_val[0] if len(current_val) == 1 else "\n".join(current_val)
                data[current_key] = value.strip()
                current_val = []
            current_key = m.group(1).strip()
            if m.group(2):
                current_val.append(m.group(2))
        elif current_key:
            current_val.append(line)

    if current_key:
        value = current_val[0] if len(current_val) == 1 else "\n".join(current_val)
        data[current_key] = value.strip()

    return data


def _write_synth_result(content: str, output_base: Path) -> str:
    """Parse LLM output, write direction files, return direction_id."""
    # Parse frontmatter for title
    fm = _parse_frontmatter(content)
    title = fm.get("title", "Goal Type")
    slug = _slugify(title)
    typ = fm.get("type", "feature")
    why = fm.get("why", "")
    acceptance = fm.get("acceptance", "")

    # Generate direction id
    direction_id_num = _next_direction_id(output_base / ".direction_counter")
    direction_id = f"{direction_id_num}-{slug}"
    direction_dir = output_base / direction_id
    direction_dir.mkdir(parents=True, exist_ok=True)

    # Write direction.md with structured frontmatter
    md_lines = [
        "---",
        f"title: {title}",
        f"type: {typ}",
        f"why: {why}",
        "acceptance: |",
    ]
    for line in acceptance.splitlines():
        md_lines.append(f"  {line.strip()}")
    md_lines.append("---")
    md_lines.append("")
    md_lines.append(content[content.find("---\n", content.find("---\n") + 4) + 4:] if content.count("---") >= 4 else f"# {title}")
    (direction_dir / "direction.md").write_text("\n".join(md_lines))

    # Write flow.md if there's Flow section in body
    body_start = content.find("---\n", 4) + 4 if content.startswith("---") else 0
    body = content[body_start:]
    if "## Flow" in body or "## flow" in body:
        (direction_dir / "flow.md").write_text(body)

    # Write state.yaml
    state = (
        "status: queued\n"
        f"created_at: {datetime.now(timezone.utc).isoformat()}\n"
        f"direction_id: {direction_id}\n"
        "pr_url: null\n"
        "summary: ''\n"
    )
    (direction_dir / "state.yaml").write_text(state)

    return direction_id


def _next_direction_id(counter_path: Path) -> str:
    """Allocate the next direction id from counter + existing directories.

    Scans sibling direction directories for numeric prefixes and allocates
    max(existing_dir_ids, counter_value) + 1.  Uses an advisory file lock
    (flock) so that concurrent requests allocating IDs under the same
    mounted volume produce unique, never-duplicate ids.
    """
    import fcntl

    counter_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(str(counter_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            raw = os.read(fd, 64).decode("utf-8").strip()
            counter_val = int(raw) if raw else 0

            # Scan existing direction directories for their numeric prefixes.
            max_dir_id = 0
            try:
                for entry in os.listdir(str(counter_path.parent)):
                    parts = entry.split("-", 1)
                    if parts[0].isdigit():
                        max_dir_id = max(max_dir_id, int(parts[0]))
            except FileNotFoundError:
                pass

            current = max(counter_val, max_dir_id) + 1
            os.lseek(fd, 0, os.SEEK_SET)
            os.truncate(fd, 0)
            os.write(fd, str(current).encode("utf-8"))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

    return f"{current:03d}"


def write_direction(
    direction_data: dict,
    *,
    parent_direction: str | None = None,
) -> dict:
    """Write the direction directory to disk and return metadata.

    Returns dict with direction_id, direction_dir, status, etc.

    The direction.md always starts with a YAML frontmatter block delimited
    by --- lines containing title, type, why, acceptance, and optionally
    parent_direction.
    """
    directions_root = Path(settings.directions_output_path)
    counter_file = directions_root / ".direction_counter"

    direction_id_num = _next_direction_id(counter_file)
    slug = direction_data.get("slug", _slugify(direction_data.get("title", "goal-type")))
    direction_id = f"{direction_id_num}-{slug}"
    direction_dir = directions_root / direction_id
    direction_dir.mkdir(parents=True, exist_ok=True)

    # Build YAML frontmatter
    title = direction_data.get("title", slug)
    why = direction_data.get("why", "")
    acceptance = direction_data.get("acceptance", [])

    md_lines = [
        "---",
        f"title: {title}",
        "type: feature",
    ]
    if parent_direction:
        md_lines.append(f"parent_direction: {parent_direction}")
    md_lines.append(f"why: {why}")
    md_lines.append("acceptance: |")
    if isinstance(acceptance, str):
        for line in acceptance.splitlines():
            md_lines.append(f"  {line.strip()}")
    else:
        for ac in acceptance:
            md_lines.append(f"  - {ac}")
    md_lines.append("---")
    md_lines.append("")
    md_lines.append(f"# {title}")

    (direction_dir / "direction.md").write_text("\n".join(md_lines))

    # flow.md
    if direction_data.get("flow_md"):
        (direction_dir / "flow.md").write_text(direction_data["flow_md"])

    # api_spec.md
    if direction_data.get("api_spec_md"):
        (direction_dir / "api_spec.md").write_text(direction_data["api_spec_md"])

    # state.yaml
    state = (
        "status: queued\n"
        f"created_at: {datetime.now(timezone.utc).isoformat()}\n"
        f"direction_id: {direction_id}\n"
        "pr_url: null\n"
        "summary: ''\n"
    )
    (direction_dir / "state.yaml").write_text(state)

    return {
        "direction_id": direction_id,
        "direction_dir": str(direction_dir),
        "status": "queued",
    }


async def synthesize_and_write_direction(
    prompt_summary: str,
    llm_client,
    output_base: Path,
) -> dict:
    """Full pipeline: synthesize direction from chat, then write it to disk.

    Calls synthesize_direction (which returns a direction_id string) and then
    reads back the written direction.md to build a metadata dict compatible
    with write_direction.

    Returns: {"direction_id": str, "direction_dir": str, "status": str}
    """
    direction_id = await synthesize_direction(
        llm_client=llm_client,
        prompt_summary=prompt_summary,
        output_base=output_base,
    )
    direction_dir = output_base / direction_id
    return {
        "direction_id": direction_id,
        "direction_dir": str(direction_dir),
        "status": "queued",
    }


async def synthesize_iteration_direction(
    llm_client,
    previous_direction_id: str,
    feedback: str,
    output_base: Path,
) -> str:
    """Synthesize a new iteration direction based on user feedback.

    Takes an LLM client (mockable), the previous direction id-slug,
    the user's feedback text, and the output directory base.

    Writes the direction files and returns the assigned direction_id.
    Raises ValueError for empty feedback.
    """
    stripped = feedback.strip()
    if not stripped:
        raise ValueError("feedback:empty: Feedback must not be empty or whitespace-only.")

    system_prompt = (
        "You are a technical architect writing a follow-up software factory direction. "
        "The user has feedback on a previously-built goal type module. Produce an "
        "iteration direction as YAML frontmatter + markdown.\n\n"
        "Your output must be a YAML block (delimited by --- lines) containing these "
        "keys:\n"
        '  - "title": A short title reflecting the iteration\n'
        '  - "type": Always "feature"\n'
        '  - "parent_direction": The previous direction id-slug\n'
        '  - "why": Why this iteration exists, referencing the previous direction '
        'by id-slug ("This iterates on <id> to ...")\n'
        '  - "acceptance": A pipe-delimited block of acceptance criteria. Say '
        '"modify the existing `backend/app/goal_types/<name>/` module to address '
        "the following feedback: ...\" with the user's feedback verbatim. Do NOT "
        "restate the previous direction's acceptance criteria.\n\n"
        "Return ONLY the frontmatter + markdown, no code fences."
    )

    user_prompt = (
        f"Previous direction: {previous_direction_id}\n"
        f"User feedback: {stripped}\n"
    )

    content = await _call_llm(llm_client, system_prompt, user_prompt)

    # Parse frontmatter
    fm = _parse_frontmatter(content)
    title = fm.get("title", "Iteration")
    slug = _slugify(title)
    typ = fm.get("type", "feature")
    why = fm.get("why", f"This iterates on {previous_direction_id} to incorporate feedback.")
    acceptance = fm.get("acceptance", stripped)

    # Reject chain-position / lineage slugs (anything starting with
    # "iterate-"). The slug must describe the feedback substantively, not
    # encode chain position, because concurrent allocations break
    # sequential assumptions.
    if re.match(r"^iterate-", slug):
        slug = _slugify(why) if why else _slugify(stripped)
        # If the why-based slug still looks like a lineage slug, fall
        # back to the previous direction's slug (minus its numeric prefix)
        # with "-iteration" appended.
        if not slug or re.match(r"^iterate-", slug):
            prev_slug = previous_direction_id.split("-", 1)[1] if "-" in previous_direction_id else previous_direction_id
            slug = prev_slug + "-iteration"

    direction_id_num = _next_direction_id(output_base / ".direction_counter")
    direction_id = f"{direction_id_num}-{slug}"
    direction_dir = output_base / direction_id
    direction_dir.mkdir(parents=True, exist_ok=True)

    md_lines = [
        "---",
        f"title: {title}",
        f"type: {typ}",
        f"parent_direction: {previous_direction_id}",
        f"why: {why}",
        "acceptance: |",
    ]
    for line in acceptance.splitlines():
        md_lines.append(f"  {line.strip()}")
    md_lines.append("---")
    md_lines.append("")
    body_start = content.find("---\n", 4) + 4 if content.startswith("---") else 0
    md_lines.append(content[body_start:] if body_start > 0 else f"# {title}")
    (direction_dir / "direction.md").write_text("\n".join(md_lines))

    # Write state.yaml
    state = (
        "status: queued\n"
        f"created_at: {datetime.now(timezone.utc).isoformat()}\n"
        f"direction_id: {direction_id}\n"
        "pr_url: null\n"
        "summary: ''\n"
    )
    (direction_dir / "state.yaml").write_text(state)

    return direction_id
"""Chat goal-type matching service.

Uses LLM (Azure Foundry) when configured, otherwise falls back to local
keyword-based matching against the goal-type registry catalog.
"""

import json as json_mod
import re

import httpx

from app.config import settings
from app.goal_types import registry as goal_type_registry


def _build_catalog() -> list[dict]:
    """Build a catalog of goal types for the LLM prompt."""
    catalog = []
    for name in goal_type_registry.list_types():
        gt = goal_type_registry.get_type(name)
        catalog.append({
            "name": gt.name,
            "description": gt.description,
            "sample_prompts": gt.sample_prompts,
        })
    return catalog


class LLMFailureError(Exception):
    """Raised when the upstream LLM call or response parsing fails."""


async def match_goal_type(user_message: str) -> dict:
    """Match user message to a goal type.

    Returns:
        {"match": "<name>"|"none", "confidence": 0..1, "rationale": "<str>"}

    Raises:
        LLMFailureError: When the LLM is configured but the upstream call
            or response parsing fails.  Callers should surface this as
            HTTP 502 per the API spec.
    """
    if settings.azure_foundry_endpoint and settings.azure_foundry_api_key:
        result = await _llm_match(user_message)
        # Preserve the LLM's actual match name and confidence so callers
        # can distinguish "no match" from "weak match below threshold".
        # LLM transport/parse failures are raised as LLMFailureError and
        # surfaced as HTTP 502 by the route — they are never coerced here.
        return result
    return _local_match(user_message)


async def _llm_match(user_message: str) -> dict:
    """Use Azure Foundry LLM to match user message to a goal type."""
    catalog = _build_catalog()

    system_prompt = (
        "You are a goal-type classifier. Given a user's natural-language goal "
        "description and a catalog of available goal types, determine which "
        "goal type best matches the user's intent. Return a JSON object with "
        "keys: 'match' (the goal type name, or 'none' if no good match), "
        "'confidence' (a float between 0 and 1), and 'rationale' (a brief "
        "explanation of your decision)."
    )

    user_prompt = (
        f"User message: {user_message}\n\n"
        f"Available goal types:\n{json_mod.dumps(catalog, indent=2)}\n\n"
        "Which goal type best matches the user's intent? "
        'Return JSON: {"match": "<name>|none", "confidence": 0..1, "rationale": "..."}'
    )

    headers = {
        "Authorization": f"Bearer {settings.azure_foundry_api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                settings.azure_foundry_endpoint,
                headers=headers,
                json=payload,
                timeout=30,
            )
        if resp.status_code != 200:
            raise LLMFailureError(f"LLM returned HTTP {resp.status_code}")

        result = resp.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = json_mod.loads(content)
        return {
            "match": parsed.get("match", "none"),
            "confidence": float(parsed.get("confidence", 0.0)),
            "rationale": parsed.get("rationale", ""),
        }
    except LLMFailureError:
        raise
    except Exception as exc:
        raise LLMFailureError(f"LLM call failed: {exc}") from exc


def _local_match(user_message: str) -> dict:
    """Local keyword-based matching fallback when no LLM is configured."""
    message_lower = user_message.lower()
    best_match = "none"
    best_confidence = 0.0
    best_rationale = "No matching goal type found"

    for name in goal_type_registry.list_types():
        gt = goal_type_registry.get_type(name)
        score = 0.0
        hits = []

        # Check name keywords in message
        name_keywords = name.replace("_", " ").split()
        name_hits = sum(1 for kw in name_keywords if kw in message_lower)
        if name_hits > 0:
            score += 0.4 * (name_hits / len(name_keywords))
            hits.append(f"name match: {name}")

        # Check description keywords
        desc_lower = gt.description.lower()
        desc_words = set(desc_lower.split()) - {"the", "a", "an", "is", "and", "or", "to", "of", "in", "for", "on", "with"}
        desc_hits = sum(1 for w in desc_words if w in message_lower)
        if desc_words:
            score += 0.3 * (desc_hits / len(desc_words))
            if desc_hits > 0:
                hits.append("description match")

        # Check sample prompts
        for prompt in gt.sample_prompts:
            prompt_words = set(prompt.lower().split()) - {"the", "a", "an", "is", "and", "or", "to", "of", "in", "for", "on", "with"}
            prompt_hits = sum(1 for w in prompt_words if w in message_lower)
            if prompt_words:
                prompt_score = 0.3 * (prompt_hits / len(prompt_words))
                if prompt_score > 0:
                    score += prompt_score
                    hits.append("sample prompt match")
                    break

        # Boost for "youtube" keyword especially
        if name == "youtube_video" and ("youtube" in message_lower or "walkthrough" in message_lower):
            score = max(score, 0.85)
            hits.append("youtube keyword boost")

        if score > best_confidence:
            best_confidence = min(score, 1.0)
            best_match = name
            best_rationale = "; ".join(hits) if hits else f"Weak match to {name}"

    return {
        "match": best_match,
        "confidence": best_confidence,
        "rationale": best_rationale,
    }


def extract_pledge_amount(user_message: str) -> int | None:
    """Extract pledge amount in cents from user message.

    Handles patterns like '$20', '$20.00', '20 dollars', 'pledge $20'.
    """
    # $20 or $20.00
    m = re.search(r'\$(\d+(?:\.\d{2})?)', user_message)
    if m:
        dollars = float(m.group(1))
        return int(dollars * 100)

    # 20 dollars
    m = re.search(r'(\d+)\s*dollars?', user_message, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 100

    # plain number after "pledge"
    m = re.search(r'pledge\s+(\d+)', user_message, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 100

    return None


def extract_title(user_message: str) -> str:
    """Extract a sensible goal title from user message."""
    # Remove leading phrases like "I want to", "I need to"
    cleaned = re.sub(r'^(i\s+(want|need|have)\s+to\s+)', '', user_message, flags=re.IGNORECASE)
    # Remove trailing "by <date>" or "by Friday"
    cleaned = re.sub(r'\s+by\s+\S+(\s+\S+)*\s*$', '', cleaned, flags=re.IGNORECASE)
    # Remove pledge amount
    cleaned = re.sub(r'\s+(and\s+)?pledge\s+\$?\d+(\s+dollars?)?', '', cleaned, flags=re.IGNORECASE)
    # Capitalize first letter
    cleaned = cleaned.strip().rstrip(".")
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned or user_message


def get_missing_criteria(goal_type_name: str, draft_goal: dict) -> list[str]:
    """Return list of criterion field names still missing from draft_goal.

    Always checks goal-level fields (charity_id, deadline) and string-type
    required criteria fields from the registry. Numeric and boolean criteria
    fields are auto-filled with defaults and not prompted for.
    """
    missing = []

    # Top-level required fields for goal creation
    if not draft_goal.get("charity_id"):
        missing.append("charity_id")
    if not draft_goal.get("deadline"):
        missing.append("deadline")

    # Goal-type specific criteria fields (string-type only)
    try:
        gt = goal_type_registry.get_type(goal_type_name)
        criteria_schema = gt.criteria_schema
        required_criteria = criteria_schema.get("required", [])
        properties = criteria_schema.get("properties", {})
        criteria_data = draft_goal.get("criteria", {}) or {}

        for field in required_criteria:
            prop = properties.get(field, {})
            # Only prompt for string fields — numeric/boolean get defaults
            if prop.get("type") == "string" and (
                field not in criteria_data or criteria_data[field] is None
            ):
                missing.append(field)
    except KeyError:
        pass

    return missing


def build_goal_payload(draft_goal: dict) -> dict:
    """Build a complete goal payload from draft_goal.

    Only includes non-string criteria fields when the registry schema
    explicitly defines a ``default`` for them, so the payload never
    fabricates values the user did not supply.  Top-level field defaults
    (currency, timezone, etc.) are left to the GoalCreate schema.
    """
    payload = dict(draft_goal)
    payload.setdefault("currency", "usd")
    payload.setdefault("description", payload.get("title", ""))

    # Apply explicit schema defaults for non-string criteria fields
    # that the chat does not prompt for conversationally.  Fields without
    # an explicit default in the schema are left unset — the existing
    # GoalCreate validation will reject them with a proper 422.
    goal_type_name = payload.get("goal_type", "")
    criteria = dict(payload.get("criteria", {}) or {})
    try:
        gt = goal_type_registry.get_type(goal_type_name)
        criteria_schema = gt.criteria_schema
        properties = criteria_schema.get("properties", {})
        for field, prop in properties.items():
            if field in criteria:
                continue
            if "default" not in prop:
                continue
            criteria[field] = prop["default"]
    except KeyError:
        pass
    payload["criteria"] = criteria

    return payload


def get_criterion_prompt(field: str) -> str:
    """Return a human-readable prompt for a criterion field."""
    prompts = {
        "charity_id": "Which charity should receive the pledge if you miss this goal?",
        "deadline": "What's your deadline? (e.g., 2026-06-01T17:00:00Z)",
        "video_description": "What should the video be about? Describe what you'll cover.",
        "url": "What's the URL of your API endpoint?",
        "method": "Which HTTP method should the endpoint use? (GET, POST, etc.)",
        "expected_status": "What HTTP status code should the endpoint return? (e.g., 200)",
        "expected_body_schema": "Describe the expected response body schema (JSON).",
        "headers": "Any custom headers needed? (JSON object, or 'none')",
        "repo_owner": "Who owns the GitHub repository? (username or org)",
        "repo_name": "What's the name of the GitHub repository?",
        "branch": "Which branch should be checked? (default: main)",
        "min_commits": "How many commits are required?",
        "required_files": "Which files must exist? (comma-separated)",
        "require_pr": "Must there be a pull request? (yes/no)",
        "repo_url": "What's the repository URL?",
        "test_command": "What test command should be run? (e.g., 'pytest')",
        "language": "What programming language? (e.g., python, javascript)",
        "env_vars": "Any environment variables needed? (JSON object, or 'none')",
        "goal_description": "Describe what the code should accomplish.",
    }
    return prompts.get(field, f"Please provide a value for '{field}'.")
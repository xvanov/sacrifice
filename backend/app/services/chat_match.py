"""Chat goal-type matching service.

Builds a goal-type catalog from the D007 registry and calls a configured LLM
to match a user message to the best goal type.  Only one LLM call per chat turn.
"""

from __future__ import annotations

import json as json_mod
import logging
from typing import Any

import httpx

from app.config import settings
from app.goal_types.registry import get_type, list_types

logger = logging.getLogger(__name__)


class ChatMatchError(RuntimeError):
    """Raised when the upstream LLM match call fails transiently.

    Catchers (e.g. the chat route) should map this to a 502 so the client
    can retry, while letting unexpected exceptions propagate as 500s.
    """


def _build_catalog() -> list[dict[str, Any]]:
    """Return the goal-type catalog used in the match prompt."""
    catalog: list[dict[str, Any]] = []
    for name in list_types():
        try:
            gt = get_type(name)
        except KeyError:
            continue
        catalog.append(
            {
                "name": gt.name,
                "description": gt.description,
                "sample_prompts": gt.sample_prompts,
            }
        )
    return catalog


async def match(user_message: str, chat_context: list[dict] | None = None) -> dict:
    """Run one LLM call to match *user_message* against the goal-type catalog.

    Returns a dict with keys ``match`` (str — goal-type name or ``"none"``),
    ``confidence`` (float), and ``rationale`` (str).
    """
    catalog = _build_catalog()

    system_prompt = (
        "You are a goal-type classifier. You will be given a catalog of known "
        "goal types (each with a name, description, and sample prompts) and a "
        "user message describing something they want to commit to. "
        "Return ONLY a JSON object with three keys:\n"
        '  - "match": the name of the best-matching goal type, or "none" if no type fits well\n'
        '  - "confidence": a number between 0 and 1\n'
        '  - "rationale": a short explanation of why you chose that match (or why none)\n'
        "Do NOT include any text outside the JSON object."
    )

    catalog_json = json_mod.dumps(catalog, indent=2)

    context_block = ""
    if chat_context:
        recent = [
            {"role": m["role"], "content": m.get("content", "")}
            for m in chat_context[-6:]  # last 6 messages
        ]
        context_block = (
            "Previous conversation:\n"
            + json_mod.dumps(recent, indent=2)
            + "\n\n"
        )

    user_prompt = (
        f"{context_block}"
        f"Catalog:\n{catalog_json}\n\n"
        f"User message: {user_message}\n\n"
        "Which goal type matches best? Return only the JSON object."
    )

    if not settings.azure_foundry_endpoint:
        logger.warning("No Azure Foundry endpoint configured; returning no-match fallback.")
        return {"match": "none", "confidence": 0.0, "rationale": "LLM not configured"}

    headers = {
        "Authorization": f"Bearer {settings.azure_foundry_api_key}",
        "Content-Type": "application/json",
    }

    payload: dict = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 400,
    }
    if settings.chat_match_model_id:
        payload["model"] = settings.chat_match_model_id

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            settings.azure_foundry_endpoint,
            headers=headers,
            json=payload,
            timeout=30,
        )

    if resp.status_code != 200:
        raise ChatMatchError(
            f"LLM returned status {resp.status_code}: {resp.text[:500]}"
        )

    result = resp.json()
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

    try:
        parsed = json_mod.loads(content.strip())
    except (json_mod.JSONDecodeError, ValueError) as exc:
        raise ChatMatchError(f"Could not parse LLM JSON response: {content[:200]}") from exc

    # Validate basic shape
    if "match" not in parsed:
        raise ChatMatchError(f"LLM response missing 'match' key: {parsed}")

    return {
        "match": str(parsed.get("match", "none")).lower(),
        "confidence": float(parsed.get("confidence", 0.0)),
        "rationale": str(parsed.get("rationale", "")),
    }
"""Chat matching service: build a catalog from the goal-type registry and
match a user message + context to a goal type via a single structured LLM call.

The module is designed to be unit-testable with a mocked LLM client —
inject a callable via ``llm_client`` rather than importing the real
httpx-based caller directly.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.goal_types.registry import get_type, list_types


class ChatMatchError(RuntimeError):
    """Raised when the upstream LLM call fails, so callers can map to 502/5xx."""


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    description: str
    sample_prompts: list[str]


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    goal_type: str | None
    confidence: float
    rationale: str
    raw_response: str | None = None


def build_catalog() -> list[CatalogEntry]:
    """Build the catalog from the auto-discovered goal-type registry.

    Each entry includes ``name``, ``description``, and ``sample_prompts``
    as specified by the D007 contract.
    """
    entries: list[CatalogEntry] = []
    for name in list_types():
        gt = get_type(name)
        entries.append(
            CatalogEntry(
                name=gt.name,
                description=gt.description,
                sample_prompts=list(gt.sample_prompts),
            )
        )
    return entries


def build_system_prompt(catalog: list[CatalogEntry]) -> str:
    catalog_lines: list[str] = []
    for entry in catalog:
        prompts = "\n".join(f"    - {p}" for p in entry.sample_prompts)
        catalog_lines.append(
            f"- name: {entry.name}\n"
            f"  description: {entry.description}\n"
            f"  sample_prompts:\n{prompts}"
        )
    catalog_text = "\n".join(catalog_lines)

    return (
        "You are a goal-type classifier. Given a user message describing a goal, "
        "match it to the most appropriate goal type from the catalog below. "
        "If no goal type fits, respond with `none`.\n\n"
        "CATALOG:\n"
        f"{catalog_text}\n\n"
        "Respond with ONLY a JSON object — no markdown, no extra text:\n"
        '{"match": "<goal_type_name or none>", "confidence": 0.0, "rationale": "<brief explanation>"}'
    )


def parse_match_response(raw: str) -> dict[str, Any] | None:
    """Parse and validate the LLM JSON response.

    Returns a dict with keys ``match``, ``confidence``, ``rationale`` on
    success, or ``None`` if the response is malformed.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None
    if "match" not in data or "confidence" not in data or "rationale" not in data:
        return None
    if not isinstance(data["match"], str):
        return None
    if isinstance(data["confidence"], bool):
        return None
    if not isinstance(data["confidence"], (int, float)):
        return None
    if not isinstance(data["rationale"], str):
        return None
    if not (0.0 <= data["confidence"] <= 1.0):
        return None

    return data


def resolve_match(
    parsed: dict[str, Any],
    *,
    threshold: float | None = None,
    valid_names: set[str] | None = None,
) -> MatchResult:
    """Decide whether a parsed LLM response constitutes a match.

    Returns a ``MatchResult`` with ``matched=True`` when the LLM returned a
    non-``"none"`` match, confidence >= threshold, **and** the match name
    exists in ``valid_names`` (the registry catalog).

    ``threshold`` defaults to ``settings.chat_match_confidence_threshold``.
    ``valid_names`` defaults to the current registry names via ``build_catalog()``.
    """
    if threshold is None:
        threshold = settings.chat_match_confidence_threshold

    match_name: str = parsed["match"]
    confidence: float = float(parsed["confidence"])
    rationale: str = parsed["rationale"]

    if match_name == "none" or confidence < threshold:
        return MatchResult(
            matched=False,
            goal_type=None,
            confidence=confidence,
            rationale=rationale,
        )

    if valid_names is None:
        valid_names = {e.name for e in build_catalog()}

    if match_name not in valid_names:
        return MatchResult(
            matched=False,
            goal_type=None,
            confidence=confidence,
            rationale=f"Unknown goal type '{match_name}': {rationale}",
        )

    return MatchResult(
        matched=True,
        goal_type=match_name,
        confidence=confidence,
        rationale=rationale,
    )


async def _default_llm_client(system_prompt: str, user_prompt: str) -> str:
    """Real Azure Foundry caller for chat matching.

    Returns the raw ``content`` string from the LLM response.
    """
    headers = {
        "Authorization": f"Bearer {settings.azure_foundry_api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": settings.chat_match_model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            settings.azure_foundry_chat_url(),
            headers=headers,
            json=payload,
            timeout=30,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"LLM API error: {resp.status_code}")

    result = resp.json()
    return result.get("choices", [{}])[0].get("message", {}).get("content", "")


async def match_message(
    user_message: str,
    *,
    chat_context: list[dict[str, str]] | None = None,
    llm_client: Callable[..., Any] | None = None,
    threshold: float | None = None,
    catalog: list[CatalogEntry] | None = None,
) -> MatchResult:
    """Run one structured LLM match call against the goal-type catalog.

    Parameters
    ----------
    user_message:
        The raw user text to classify.
    chat_context:
        Optional list of prior chat messages (``role`` / ``content`` dicts).
    llm_client:
        Injectable async callable that takes ``(system_prompt, user_prompt)``
        and returns a raw JSON string (or a pre-parsed dict). Defaults to
        the real Azure Foundry caller.
    threshold:
        Confidence threshold override. Falls back to settings default.
    catalog:
        Optional registry-backed catalog supplied by the caller. Falls back to
        ``build_catalog()`` when omitted.
    """
    if llm_client is None:
        llm_client = _default_llm_client

    if catalog is None:
        catalog = build_catalog()
    system_prompt = build_system_prompt(catalog)

    # Build the user prompt with optional chat context
    context_block = ""
    if chat_context:
        context_lines = [f"{msg['role']}: {msg['content']}" for msg in chat_context]
        context_block = "CHAT CONTEXT:\n" + "\n".join(context_lines) + "\n\n"

    user_prompt = (
        f"{context_block}"
        f"USER MESSAGE: {user_message}\n\n"
        "Classify the USER MESSAGE into one of the catalog goal types."
    )

    raw = ""
    try:
        result = await llm_client(system_prompt, user_prompt)
    except Exception as exc:
        raise ChatMatchError(f"Upstream LLM call failed: {exc}") from exc

    if isinstance(result, dict):
        if "match" in result and "confidence" in result:
            parsed = result
        else:
            raw = result.get("content", "")
            if isinstance(raw, str):
                parsed = parse_match_response(raw)
            else:
                return _no_match("LLM returned unrecognized dict shape")
    elif isinstance(result, str):
        raw = result
        parsed = parse_match_response(raw)
    else:
        return _no_match(f"Unexpected LLM response type: {type(result)}")

    if parsed is None:
        return _no_match("Could not parse LLM response", raw_response=raw)

    return resolve_match(
        parsed,
        threshold=threshold,
        valid_names={entry.name for entry in catalog},
    )


def _no_match(rationale: str, raw_response: str | None = None) -> MatchResult:
    return MatchResult(
        matched=False,
        goal_type=None,
        confidence=0.0,
        rationale=rationale,
        raw_response=raw_response,
    )

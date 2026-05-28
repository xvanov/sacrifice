from typing import Any

from app.goal_types.registry import get_type, list_types


def build_catalog_summary() -> list[dict[str, Any]]:
    """Build a lightweight catalog from the goal-type registry for the LLM prompt.

    Returns a list of dicts with keys: name, description, sample_prompts.
    """
    result = []
    for name in sorted(list_types()):
        gt = get_type(name)
        result.append({
            "name": name,
            "description": gt.description,
            "sample_prompts": gt.sample_prompts,
        })
    return result


class ChatMatchService:
    """Service for matching user chat messages to goal types via LLM.

    Designed to be unit-testable: pass a callable `llm_client` that accepts a
    prompt string and returns a JSON-parsed response dict.

    The LLM is expected to return a structured JSON response:
        {"match": "<goal_type_name>" | "none", "confidence": 0..1, "rationale": "<str>"}
    """

    def __init__(self, llm_client, confidence_threshold: float = 0.7):
        self._llm = llm_client
        self._threshold = confidence_threshold

    async def match(
        self,
        user_message: str,
        chat_context: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Match a user message against the goal-type catalog.

        Returns a dict with keys:
          - match: str (goal type name or "none")
          - confidence: float
          - rationale: str
          - above_threshold: bool
        """
        catalog_entries = build_catalog_summary()

        if not catalog_entries:
            return {
                "match": "none",
                "confidence": 0.0,
                "rationale": "No goal types in catalog.",
                "above_threshold": False,
            }

        prompt = self._build_prompt(user_message, chat_context or [], catalog_entries)
        raw = await self._llm(prompt)
        result = self._parse_response(raw)
        result["above_threshold"] = (
            result["match"] != "none" and result["confidence"] >= self._threshold
        )
        return result

    def _build_prompt(
        self,
        user_message: str,
        chat_context: list[dict[str, Any]],
        catalog_entries: list[dict[str, Any]],
    ) -> str:
        catalog_lines = []
        for entry in catalog_entries:
            samples = "; ".join(entry["sample_prompts"][:3])
            catalog_lines.append(
                f"- {entry['name']}: {entry['description']} "
                f"(sample prompts: {samples})"
            )

        context_lines = ""
        if chat_context:
            recent = chat_context[-6:]  # last 6 messages for context
            context_lines = "\n".join(
                f"[{m.get('role', 'unknown')}]: {m.get('content', '')[:200]}"
                for m in recent
            )

        return (
            "You are a goal-type classifier. Given a user's natural-language goal "
            "description and a catalog of available goal types, determine which "
            "goal type best matches.\n\n"
            "CATALOG:\n"
            f"{chr(10).join(catalog_lines)}\n\n"
            f"CHAT CONTEXT (recent):\n{context_lines}\n\n"
            f"USER MESSAGE:\n{user_message}\n\n"
            "Respond with a single JSON object:\n"
            '{"match": "<goal_type_name>|none", "confidence": 0.0-1.0, '
            '"rationale": "<brief explanation>"}'
        )

    def _parse_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        match = raw.get("match", "none")
        if match not in self._valid_match_names():
            match = "none"
        confidence = float(raw.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
        return {
            "match": match,
            "confidence": confidence,
            "rationale": str(raw.get("rationale", "")),
        }

    def _valid_match_names(self) -> set[str]:
        return {entry["name"] for entry in build_catalog_summary()} | {"none"}

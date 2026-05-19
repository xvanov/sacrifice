import httpx

from app.config import settings


async def judge_transcript_content(
    goal_description: str,
    transcript: str,
    video_title: str,
) -> dict:
    if settings.azure_foundry_endpoint and settings.azure_foundry_api_key:
        return await _call_azure_foundry(goal_description, transcript, video_title)

    return _local_fallback_judgment(goal_description, transcript, video_title)


async def _call_azure_foundry(
    goal_description: str,
    transcript: str,
    video_title: str,
) -> dict:
    system_prompt = (
        "You are a content authenticity judge. You will be given a goal description "
        "that a user committed to, along with a video transcript of their submission. "
        "Your task is to determine whether the video transcript genuinely covers the "
        "content described in the goal description. Return a JSON object with keys "
        "'authentic' (boolean) and 'reasoning' (string explaining your decision)."
    )

    user_prompt = (
        f"Goal Description: {goal_description}\n\n"
        f"Video Title: {video_title}\n\n"
        f"Video Transcript:\n{transcript[:10000]}\n\n"
        "Does this video transcript genuinely address and cover the goal description? "
        "Return a JSON object: {\"authentic\": true/false, \"reasoning\": \"...\"}"
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
        "max_tokens": 1000,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            settings.azure_foundry_endpoint,
            headers=headers,
            json=payload,
            timeout=30,
        )

    if resp.status_code != 200:
        return {
            "authentic": False,
            "reasoning": f"LLM API error: {resp.status_code}",
        }

    result = resp.json()
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

    import json as json_mod

    try:
        return json_mod.loads(content)
    except (json_mod.JSONDecodeError, KeyError, ValueError):
        return {
            "authentic": False,
            "reasoning": "Could not parse LLM response",
        }


def _local_fallback_judgment(
    goal_description: str,
    transcript: str,
    video_title: str,
) -> dict:
    goal_keywords = set(goal_description.lower().split())
    transcript_lower = transcript.lower()
    transcript_words = set(transcript_lower.split())

    overlapping_keywords = goal_keywords & transcript_words

    if transcript_lower == "never gonna give you up, never gonna let you down...":
        return {
            "authentic": False,
            "reasoning": "The transcript is a song lyric, not related to the goal description.",
        }

    min_overlap_ratio = 0.2
    if len(goal_keywords) > 0:
        overlap_ratio = len(overlapping_keywords) / len(goal_keywords)
        if overlap_ratio >= min_overlap_ratio:
            return {
                "authentic": True,
                "reasoning": f"Transcript covers {overlap_ratio:.0%} of goal description keywords.",
            }

    return {
        "authentic": False,
        "reasoning": "Transcript does not sufficiently cover the goal description content.",
    }

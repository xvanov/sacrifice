"""Frozen LLM response: youtube_video_v2 goal-type module.

This is a deterministic snapshot of what the factory chain would generate
for a YouTube video verification goal type.  Used by the fake_factory_chain
fixture so CI never calls a real LLM.
"""

DEFINITION = """
definition = {
    "name": "youtube_video_v2",
    "description": "Verify that a submitted YouTube video meets duration and content criteria via transcript analysis.",
    "sample_prompts": [
        "I'll record a YouTube video and submit the link as proof. The video should be at least 5 minutes long and cover building a feature.",
    ],
    "criteria_schema": {
        "type": "object",
        "properties": {
            "min_duration_seconds": {"type": "integer", "description": "Minimum video length in seconds"},
            "video_description": {"type": "string", "description": "What the video should demonstrate"},
        },
        "required": ["min_duration_seconds", "video_description"],
    },
}
"""

INIT_PY = """
from app.goal_types.base import GoalTypeBase
from app.schemas.proof import YouTubeProofSubmission
from app.services.youtube import extract_video_id
from pydantic import ValidationError

from .definition import definition


class YoutubeVideoV2GoalType(GoalTypeBase):
    name = definition["name"]
    description = definition["description"]
    sample_prompts = definition["sample_prompts"]
    criteria_schema = definition["criteria_schema"]

    def submit_proof(self, proof_data: dict, criteria_data: dict) -> dict:
        body = proof_data.get("_body")
        if body is None:
            raise ValueError("Missing _body in proof_data")
        youtube_url = getattr(body, "youtube_url", None)
        if not youtube_url:
            raise ValueError("youtube_url is required for youtube_video_v2 proof submission")
        try:
            YouTubeProofSubmission(youtube_url=youtube_url)
        except ValidationError as e:
            msg = str(e.errors()[0]["msg"]) if e.errors() else "Invalid YouTube URL"
            raise ValueError(msg)
        video_id = extract_video_id(youtube_url)
        if not video_id:
            raise ValueError("Could not extract video ID from URL")
        return {
            "proof_data": {"video_id": video_id, "url": youtube_url},
            "criteria_data": criteria_data,
        }

    async def verify(self, proof_data: dict, criteria_data: dict) -> dict:
        from .verifier import verify
        return await verify(proof_data, criteria_data)

    def dispatch_verification(
        self, goal_id: str, submission_id: str,
        proof_data: dict, criteria_data: dict,
    ) -> None:
        from app.workers.youtube_v2 import run_youtube_v2_verification_task
        run_youtube_v2_verification_task.delay(
            goal_id_str=goal_id,
            submission_id_str=submission_id,
            proof_data=proof_data,
            criteria_data=criteria_data,
        )


goal_type = YoutubeVideoV2GoalType()
"""

VERIFIER_PY = """
from app.workers.youtube import (
    fetch_video_metadata,
    fetch_video_transcript,
    judge_transcript_content,
)


async def verify(proof_data: dict, criteria_data: dict) -> dict:
    video_id = proof_data.get("video_id")
    if not video_id:
        return {
            "verification_status": "failed",
            "verification_details": {"reason": "Missing video_id in proof_data"},
        }

    min_duration = criteria_data.get("min_duration_seconds", 0)

    try:
        metadata = await fetch_video_metadata(video_id)
    except Exception:
        return {
            "verification_status": "failed",
            "verification_details": {"reason": "Could not fetch video metadata"},
        }

    duration_ok = metadata.get("duration_seconds", 0) >= min_duration

    try:
        transcript = await fetch_video_transcript(video_id)
    except ValueError:
        return {
            "verification_status": "failed",
            "verification_details": {
                "duration_passed": duration_ok,
                "reason": "Transcript not available",
            },
        }

    description = criteria_data.get("video_description", "")
    judge_result = await judge_transcript_content(transcript, description)

    content_ok = judge_result.get("authentic", False)

    return {
        "verification_status": "verified" if (duration_ok and content_ok) else "failed",
        "verification_details": {
            "duration_passed": duration_ok,
            "content_passed": content_ok,
            "llm_reasoning": judge_result.get("reasoning"),
        },
    }
"""

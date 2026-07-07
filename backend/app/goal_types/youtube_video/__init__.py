"""youtube_video goal type plugin."""

from pydantic import ValidationError

from app.goal_types.base import GoalTypeBase, ProofTypeMismatch
from app.schemas.proof import YouTubeProofSubmission
from app.services.youtube import extract_video_id

from .definition import definition


class YoutubeVideoGoalType(GoalTypeBase):
    name = definition["name"]
    description = definition["description"]
    sample_prompts = definition["sample_prompts"]
    criteria_schema = definition["criteria_schema"]

    def submit_proof(self, proof_data: dict, criteria_data: dict) -> dict:
        body = proof_data.get("_body")
        if body is None:
            raise ValueError("Missing _body in proof_data")

        if getattr(body, "url", None) or getattr(body, "method", None):
            raise ProofTypeMismatch(
                "Proof submission type mismatch: goal is 'youtube_video', not 'api_endpoint'"
            )

        youtube_url = getattr(body, "youtube_url", None)
        if not youtube_url:
            raise ValueError("youtube_url is required for youtube_video proof submission")

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
        from app.workers.youtube import run_youtube_verification_task
        run_youtube_verification_task.delay(
            goal_id_str=goal_id,
            submission_id_str=submission_id,
            proof_data=proof_data,
            criteria_data=criteria_data,
        )


goal_type = YoutubeVideoGoalType()
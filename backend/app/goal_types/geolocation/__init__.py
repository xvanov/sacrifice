"""geolocation goal type plugin."""

from pydantic import ValidationError

from app.goal_types.base import GoalTypeBase, ProofTypeMismatch
from app.schemas.proof import GeolocationProofSubmission

from .definition import definition


class GeolocationGoalType(GoalTypeBase):
    name = definition["name"]
    description = definition["description"]
    sample_prompts = definition["sample_prompts"]
    criteria_schema = definition["criteria_schema"]

    def submit_proof(self, proof_data: dict, criteria_data: dict) -> dict:
        body = proof_data.get("_body")
        if body is None:
            raise ValueError("Missing _body in proof_data")

        if getattr(body, "youtube_url", None) or getattr(body, "repo_url", None):
            raise ProofTypeMismatch(
                "Proof submission type mismatch: goal is 'geolocation'"
            )

        latitude = getattr(body, "latitude", None)
        longitude = getattr(body, "longitude", None)
        if latitude is None or longitude is None:
            raise ValueError(
                "latitude and longitude are required for geolocation proof submission"
            )

        accuracy_m = getattr(body, "accuracy_m", None)
        try:
            GeolocationProofSubmission(
                latitude=latitude, longitude=longitude, accuracy_m=accuracy_m
            )
        except ValidationError as e:
            msg = str(e.errors()[0]["msg"]) if e.errors() else "Invalid geolocation proof data"
            raise ValueError(msg)

        return {
            "proof_data": {
                "latitude": latitude,
                "longitude": longitude,
                "accuracy_m": accuracy_m,
            },
            "criteria_data": dict(criteria_data),
        }

    async def verify(self, proof_data: dict, criteria_data: dict) -> dict:
        from app.workers.geolocation import verify_geolocation

        return await verify_geolocation(proof_data, criteria_data)

    def dispatch_verification(
        self, goal_id: str, submission_id: str,
        proof_data: dict, criteria_data: dict,
    ) -> None:
        from app.workers.geolocation import run_geolocation_verification_task

        run_geolocation_verification_task.delay(
            goal_id_str=goal_id,
            submission_id_str=submission_id,
            proof_data=proof_data,
            criteria_data=criteria_data,
        )


goal_type = GeolocationGoalType()

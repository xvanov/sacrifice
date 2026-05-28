"""api_endpoint goal type plugin."""

from pydantic import ValidationError

from app.goal_types.base import GoalTypeBase
from app.schemas.proof import ApiEndpointProofSubmission

from .definition import definition


class ApiEndpointGoalType(GoalTypeBase):
    name = definition["name"]
    description = definition["description"]
    sample_prompts = definition["sample_prompts"]
    criteria_schema = definition["criteria_schema"]

    def submit_proof(self, proof_data: dict, criteria_data: dict) -> dict:
        from app.goal_types.base import TypeMismatchError

        if proof_data.get("youtube_url"):
            raise TypeMismatchError(
                "Proof submission type mismatch: goal is 'api_endpoint', not 'youtube_video'"
            )

        url = proof_data.get("url")
        if not url:
            raise ValueError("url is required for api_endpoint proof submission")

        method = proof_data.get("method") or "GET"
        headers = proof_data.get("headers")
        expected_status = proof_data.get("expected_status")
        expected_body_schema = proof_data.get("expected_body_schema")

        try:
            ApiEndpointProofSubmission(
                url=url, method=method,
                headers=headers,
                expected_status=expected_status,
                expected_body_schema=expected_body_schema,
            )
        except ValidationError as e:
            msg = str(e.errors()[0]["msg"]) if e.errors() else "Invalid API endpoint proof data"
            raise ValueError(msg)

        overridden = dict(criteria_data)
        overridden["url"] = url
        overridden["method"] = method
        if headers is not None:
            overridden["headers"] = headers
        if expected_status is not None:
            overridden["expected_status"] = expected_status
        if expected_body_schema is not None:
            overridden["expected_body_schema"] = expected_body_schema

        return {
            "proof_data": {
                "url": url,
                "method": method,
                "headers": headers,
                "expected_status": expected_status,
                "expected_body_schema": expected_body_schema,
            },
            "criteria_data": overridden,
        }

    async def verify(self, proof_data: dict, criteria_data: dict) -> dict:
        from .verifier import verify
        return await verify(proof_data, criteria_data)

    def dispatch_verification(
        self, goal_id: str, submission_id: str,
        proof_data: dict, criteria_data: dict,
    ) -> None:
        from app.workers.api_check import run_api_verification_task
        run_api_verification_task.delay(
            goal_id_str=goal_id,
            submission_id_str=submission_id,
            proof_data=proof_data,
            criteria_data=criteria_data,
        )


goal_type = ApiEndpointGoalType()
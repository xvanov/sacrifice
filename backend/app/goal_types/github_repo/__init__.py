"""github_repo goal type plugin."""

from app.core.crypto import encrypt_token
from app.goal_types.base import GoalTypeBase

from .definition import definition


class GithubRepoGoalType(GoalTypeBase):
    name = definition["name"]
    description = definition["description"]
    sample_prompts = definition["sample_prompts"]
    criteria_schema = definition["criteria_schema"]

    def submit_proof(self, proof_data: dict, criteria_data: dict) -> dict:
        body = proof_data.get("_body")
        if body is None:
            raise ValueError("Missing _body in proof_data")

        repo_url = getattr(body, "repo_url", None)
        if not repo_url:
            raise ValueError("repo_url is required for github_repo proof submission")

        branch = getattr(body, "branch", None) or "main"
        github_token = getattr(body, "github_token", None)
        encrypted_token = encrypt_token(github_token) if github_token else None

        overridden = dict(criteria_data)
        overridden["repo_url"] = repo_url
        overridden["branch"] = branch
        if github_token:
            overridden["github_token"] = encrypted_token

        return {
            "proof_data": {
                "repo_url": repo_url,
                "branch": branch,
                "github_token": encrypted_token,
            },
            "criteria_data": overridden,
        }

    async def verify(self, proof_data: dict, criteria_data: dict) -> dict:
        from .verifier import verify

        return await verify(proof_data, criteria_data)

    def dispatch_verification(
        self,
        goal_id: str,
        submission_id: str,
        proof_data: dict,
        criteria_data: dict,
    ) -> None:
        from app.workers.github_repo import run_github_repo_verification_task

        run_github_repo_verification_task.delay(
            goal_id_str=goal_id,
            submission_id_str=submission_id,
            proof_data=proof_data,
            criteria_data=criteria_data,
        )


goal_type = GithubRepoGoalType()

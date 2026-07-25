"""dev_sandbox goal type plugin."""

from app.goal_types.base import GoalTypeBase

from .definition import definition


class DevSandboxGoalType(GoalTypeBase):
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
            raise ValueError("repo_url is required for dev_sandbox proof submission")

        branch = getattr(body, "branch", None) or criteria_data.get("branch", "main")
        test_command = getattr(body, "test_command", None) or criteria_data.get(
            "test_command", "python -m pytest -v"
        )

        # Reject an unusable test command here (ValueError -> HTTP 400) rather
        # than in the worker, where every `failed` verdict charges the pledge.
        from app.workers.dev_sandbox import parse_test_command

        parse_test_command(test_command)
        language = getattr(body, "language", None)
        env_vars = getattr(body, "env_vars", None)

        overridden = dict(criteria_data)
        overridden["repo_url"] = repo_url
        overridden["branch"] = branch
        overridden["test_command"] = test_command
        if language:
            overridden["language"] = language
        if env_vars is not None:
            overridden["env_vars"] = env_vars

        return {
            "proof_data": {
                "repo_url": repo_url,
                "branch": branch,
                "test_command": test_command,
                "language": language,
                "env_vars": env_vars,
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
        from app.workers.dev_sandbox import run_dev_sandbox_verification_task

        run_dev_sandbox_verification_task.delay(
            goal_id_str=goal_id,
            submission_id_str=submission_id,
            proof_data=proof_data,
            criteria_data=criteria_data,
        )


goal_type = DevSandboxGoalType()

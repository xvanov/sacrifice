"""dev_sandbox goal type plugin."""

from app.core.crypto import encrypt_token
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

        # Optional PAT for a private repo. Encrypted here, exactly as
        # github_repo does it, so the plaintext never reaches the database or the
        # Celery broker; the worker decrypts it per clone.
        github_token = getattr(body, "github_token", None)
        encrypted_token = encrypt_token(github_token) if github_token else None

        overridden = dict(criteria_data)
        overridden["repo_url"] = repo_url
        overridden["branch"] = branch
        overridden["test_command"] = test_command
        if language:
            overridden["language"] = language
        if env_vars is not None:
            overridden["env_vars"] = env_vars
        # FILL-only, like the rest of this dict: a later proof that omits the
        # token can still re-verify a private repo, but an empty submission must
        # not wipe a working credential.
        if encrypted_token:
            overridden["github_token"] = encrypted_token

        return {
            "proof_data": {
                "repo_url": repo_url,
                "branch": branch,
                "test_command": test_command,
                "language": language,
                "env_vars": env_vars,
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
        from app.workers.dev_sandbox import run_dev_sandbox_verification_task

        run_dev_sandbox_verification_task.delay(
            goal_id_str=goal_id,
            submission_id_str=submission_id,
            proof_data=proof_data,
            criteria_data=criteria_data,
        )


goal_type = DevSandboxGoalType()

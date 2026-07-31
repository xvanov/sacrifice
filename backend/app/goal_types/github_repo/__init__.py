"""github_repo goal type plugin."""

from pydantic import ValidationError

from app.core.crypto import encrypt_token
from app.goal_types.base import GoalTypeBase
from app.schemas.proof import GithubRepoProofSubmission

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

        # Validate through the goal type's own schema before anything is stored.
        # The route parses the request into the permissive ``ProofSubmissionCreate``
        # (every field optional, so one body can serve five goal types), which
        # means per-type rules only apply if a type applies them here — as
        # api_endpoint and geolocation already do. Skipping it left
        # ``GithubRepoProofSubmission`` orphaned and let an unparseable repo_url
        # through to the verifier, where "cannot evaluate" is a permanent
        # inconclusive reason that makes the pledge uncollectable. Rejecting here
        # is a 422 the user can act on.
        try:
            GithubRepoProofSubmission(
                repo_url=repo_url, branch=branch, github_token=github_token
            )
        except ValidationError as e:
            msg = (
                str(e.errors()[0]["msg"])
                if e.errors()
                else "Invalid github_repo proof data"
            )
            raise ValueError(msg)

        encrypted_token = encrypt_token(github_token) if github_token else None

        # FILL, never overwrite. These keys used to be assigned unconditionally,
        # which let a proof retarget its own verification: the branch field is
        # user-editable, so submitting ``branch: "main"`` against a goal that
        # said ``feature/x`` moved the checks onto a branch full of pre-existing
        # history and passed without touching ``feature/x``. The same clobbering
        # hid a repo swap. What the goal committed to wins; the submitted values
        # only supply what the criteria never specified.
        #
        # ``branch`` is deliberately NOT filled in from the default: the schema
        # defaults it to ``"main"`` (app/schemas/proof.py:41), so writing that
        # into the criteria would bake in a guess that 404s on a ``master``
        # repository — and a 404 charges the user. Leaving it absent lets the
        # verifier ask GitHub for the repo's real default branch.
        overridden = dict(criteria_data)
        overridden.setdefault("repo_url", repo_url)
        if not overridden.get("branch") and branch != "main":
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

"""Abstract base class for goal-type plugins.

All goal-type modules MUST subclass GoalTypeBase and implement verify().
"""

from abc import ABC, abstractmethod
from typing import Any


class ProofValidationError(ValueError):
    """Proof body is malformed for its (correct) goal type → HTTP 422.

    e.g. missing required field, unparseable URL. Subclass of ValueError so
    existing ``except ValueError`` sites keep working.
    """


class ProofTypeMismatch(ProofValidationError):
    """Proof body is shaped for a DIFFERENT goal type than the goal → HTTP 400.

    e.g. an api_endpoint proof (``url``/``method``) submitted against a
    youtube_video goal. Distinct from ProofValidationError so the route can
    return 400 (wrong resource) vs 422 (bad field).
    """


class GoalTypeBase(ABC):
    """Abstract contract for a goal-type plugin.

    Subclasses must implement:
    - verify(proof_data, criteria_data) -> dict

    Subclasses may override:
    - submit_proof(proof_data, criteria_data) -> dict
    - dispatch_verification(goal_id, submission_id, proof_data, criteria_data)
    """

    name: str = ""
    description: str = ""
    sample_prompts: list[str] = []
    criteria_schema: dict = {}

    @abstractmethod
    async def verify(self, proof_data: dict, criteria_data: dict) -> dict:
        """Run verification for a proof submission.

        Returns a dict with at least:
            {"verification_status": "verified"|"failed", "verification_details": {...}}
        """
        ...

    def submit_proof(self, proof_data: dict, criteria_data: dict) -> dict:
        """Validate and extract proof data from a submission body.

        Subclasses may override to provide type-specific extraction/validation.
        Returns a dict that should be passed to verify().

        Raises RuntimeError if not overridden.
        """
        raise RuntimeError(
            f"Goal type '{self.name}' has no submit_proof implementation"
        )

    def dispatch_verification(
        self,
        goal_id: str,
        submission_id: str,
        proof_data: dict,
        criteria_data: dict,
    ) -> None:
        """Dispatch async verification (e.g., via Celery).

        The default implementation is a no-op; subclasses may override
        to enqueue a worker task.
        """
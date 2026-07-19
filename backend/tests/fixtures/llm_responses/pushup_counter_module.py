"""Frozen LLM response: pushup_counter goal-type module.

This is a deterministic snapshot of what the factory chain would generate
for a pushup-counter video verification goal type.  Used by the
fake_factory_chain fixture so CI never calls a real LLM.
"""

DEFINITION = """
definition = {
    "name": "pushup_counter",
    "description": "Verify pushup count from a workout video using pose estimation.",
    "sample_prompts": [
        "I want to do 20 pushups every morning at 7am and verify with my phone camera.",
    ],
    "criteria_schema": {
        "type": "object",
        "properties": {
            "count": {"type": "integer", "description": "Minimum number of pushups required"},
        },
        "required": ["count"],
    },
}
"""

INIT_PY = """
from app.goal_types.base import GoalTypeBase

from .definition import definition


class PushupCounterGoalType(GoalTypeBase):
    name = definition["name"]
    description = definition["description"]
    sample_prompts = definition["sample_prompts"]
    criteria_schema = definition["criteria_schema"]

    async def verify(self, proof_data: dict, criteria_data: dict) -> dict:
        from .verifier import verify
        return await verify(proof_data, criteria_data)

    def dispatch_verification(
        self, goal_id: str, submission_id: str,
        proof_data: dict, criteria_data: dict,
    ) -> None:
        from app.workers.pushup_counter import run_pushup_counter_verification_task
        run_pushup_counter_verification_task.delay(
            goal_id_str=goal_id,
            submission_id_str=submission_id,
            proof_data=proof_data,
            criteria_data=criteria_data,
        )


goal_type = PushupCounterGoalType()
"""

VERIFIER_PY = """
async def verify(proof_data: dict, criteria_data: dict) -> dict:
    required_count = criteria_data.get("count", 0)
    upload_path = proof_data.get("upload_path", "")

    if not upload_path:
        return {
            "verification_status": "failed",
            "verification_details": {"reason": "No video upload provided"},
        }

    # In production this would call a pose-estimation service.
    # The fake_factory_chain fixture patches this to return a
    # deterministic count derived from the fixture video name.
    from ._pose import count_pushups
    detected_count = count_pushups(upload_path)

    passed = detected_count >= required_count
    return {
        "verification_status": "verified" if passed else "failed",
        "verification_details": {
            "required_count": required_count,
            "detected_count": detected_count,
        },
    }
"""

POSE_PY = """
def count_pushups(video_path: str) -> int:
    '''Count pushups in a video file.

    In production this uses a pose-estimation model.  The fake_factory_chain
    fixture monkey-patches this function to return counts derived from the
    fixture video filename so CI stays deterministic.
    '''
    raise NotImplementedError("Pose estimation not available — use the fake_factory_chain fixture")
"""

WORKER_PY = """
from celery import shared_task


@shared_task(name="pushup_counter.run_pushup_counter_verification_task")
def run_pushup_counter_verification_task(
    goal_id_str: str,
    submission_id_str: str,
    proof_data: dict,
    criteria_data: dict,
):
    '''Celery task stub — real impl runs verification asynchronously.'''
    pass
"""
